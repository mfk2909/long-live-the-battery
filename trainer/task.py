import argparse
import os
from absl import logging
import datetime
import numpy as np

import tensorflow as tf
from google.cloud import storage

import trainer.data_pipeline as dp
import trainer.split_model as split_model
import trainer.constants as cst
import trainer.evaluation as ev


class CustomCheckpoints(tf.keras.callbacks.Callback):
    """
    Custom callback function with ability to save the model to GCP.
    The SavedModel contains:

    1) a checkpoint containing the model weights. (variables/)
    2) a SavedModel proto containing the Tensorflow backend 
    graph. (saved_model.pb)
    3) the model's json config. (assets/)

    For big models too many checkpoints can blow up the size of the
    log directory. To reduce the number of checkpoints, use the
    parameters below.
    
    log_dir: The base directory of all log files. Checkpoints
    will be saved in a "checkpoints" directory within this directory.
    
    dataset_path: data that is used for plotting the validation results.
    
    dataset_config: Same config file that is used for creating the training and
        validation datasets in train_and_evaluate(). This is needed to make the
        validation plot compareable.
    
    start_epoch: The epoch after which checkpoints are saved.
    
    save_best_only: Only save a model if it has a lower validation loss
    than all previously saved models.
    
    period: Save model only for every n-th epoch.
    """
    def __init__(self, log_dir, start_epoch, dataset_path, dataset_config, save_best_only=False, period=1):
        self.log_dir = log_dir
        self.start_epoch = start_epoch
        self.save_best_only = save_best_only
        self.period = period
        self.client = storage.Client()
        self.bucket = self.client.get_bucket(cst.BUCKET_NAME)  # Only used for saving evaluation plots
        self.validation_dataset = dp.create_dataset(data_dir=dataset_path,
                                                    window_size=dataset_config["window_size"],
                                                    shift=dataset_config["shift"],
                                                    stride=dataset_config["stride"],
                                                    batch_size=dataset_config["batch_size"],
                                                    cycle_length=1,  # Has to be set for plotting
                                                    num_parallel_calls=1,  # Has to be set for plotting
                                                    shuffle=False,  # Has to be set for plotting
                                                    repeat=False)  # Has to be set for plotting
    
    def on_train_begin(self, logs=None):
        self.last_saved_epoch = None
        self.lowest_loss = np.Inf
        
    def on_epoch_end(self, epoch, logs=None):
        if (epoch % self.period == 0) and (epoch >= self.start_epoch):
            self.current_loss = logs.get('val_loss')
            self.checkpoint_dir = os.path.join(self.log_dir, "checkpoints", "epoch_{}_loss_{}".format(epoch, self.current_loss))
            if self.save_best_only:
                if self.current_loss < self.lowest_loss:
                    tf.keras.experimental.export_saved_model(self.model, self.checkpoint_dir)
                    self._save_evaluation_plot(self.model, self.checkpoint_dir, self.validation_dataset)
                    self.lowest_loss = self.current_loss
                    self.last_saved_epoch = epoch
            else:
                tf.keras.experimental.export_saved_model(self.model, self.checkpoint_dir)
                self._save_evaluation_plot(self.model, self.checkpoint_dir, self.validation_dataset)

    def on_train_end(self, logs=None):
        print("Last saved model is from epoch {}".format(self.last_saved_epoch))
        
    def _save_evaluation_plot(self, model, checkpoint_dir, dataset, file_name='validation_plot.html'):
        html_dir = os.path.join(checkpoint_dir, file_name)
        
        # Make a forward pass over the whole validation dataset and get the results as a dataframe
        val_results = ev.get_predictions_results(model, dataset)
        
        # Plot the resutls with plotly and wrap the resulting <div> as a html string
        plot_div = ev.plot_predictions_and_errors(val_results)
        plot_html = "<html><body>{}</body></html>".format(plot_div)
        
        # Save the html either in google cloud or locally
        if cst.BUCKET_NAME in html_dir:
            blob = self.bucket.blob(html_dir)
            blob.upload_from_string(plot_html, content_type="text/html")
        else:
            with open(html_dir, 'w') as f:
                f.write(plot_html)


def get_args():
    """Argument parser.

    Returns:
        Dictionary of arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--job-dir',
        type=str,
        default=cst.BASE_DIR,
        help='local or GCS location for writing checkpoints and exporting models')
    parser.add_argument(
        '--data-dir-train',
        type=str,
        default=cst.TRAIN_SET,
        help='local or GCS location for reading TFRecord files for the training set')
    parser.add_argument(
        '--data-dir-validate',
        type=str,
        default=cst.TEST_SET,
        help='local or GCS location for reading TFRecord files for the validation set')
    parser.add_argument(
        '--tboard-dir',         # no default so we can construct dynamically with timestamp
        type=str,
        help='local or GCS location for reading TensorBoard files')
    parser.add_argument(
        '--saved-model-dir',    # no default so we can construct dynamically with timestamp
        type=str,
        help='local or GCS location for saving trained Keras models')
    parser.add_argument(
        '--num-epochs',
        type=int,
        default=3,
        help='number of times to go through the data, default=3')
    parser.add_argument(
        '--batch-size',
        default=16,
        type=int,
        help='number of records to read during each training step, default=16')
    parser.add_argument(
        '--window-size',
        default=20,
        type=int,
        help='window size for sliding window in training sample generation, default=100')
    parser.add_argument(
        '--shift',
        default=5,
        type=int,
        help='shift for sliding window in training sample generation, default=20')
    parser.add_argument(
        '--stride',
        default=1,
        type=int,
        help='stride inside sliding window in training sample generation, default=1')
    parser.add_argument(
        '--learning-rate',
        default=.01,      # NOT USED RIGHT NOW
        type=float,
        help='learning rate for gradient descent, default=.01')
    parser.add_argument(
        '--verbosity',
        choices=['DEBUG', 'ERROR', 'FATAL', 'INFO', 'WARN'],
        default='DEBUG')
    parser.add_argument(
        '--loss',
        default='mean_squared_error',
        type=str,
        help='loss function used by the model, default=mean_squared_error')
    parser.add_argument(
        '--shuffle',
        default=True,
        type=bool,
        help='shuffle the batched dataset, default=True'
    )
    parser.add_argument(
        '--shuffle-buffer',
        default=500,
        type=int,
        help='Bigger buffer size means better shuffling but longer setup time. Default=500'
    )
    parser.add_argument(
        '--save-from',
        default=80,
        type=int,
        help='epoch after which model checkpoints are saved, default=80'
    )
    args, _ = parser.parse_known_args()
    return args


def train_and_evaluate(args):
    """Trains and evaluates the Keras model.

    Uses the Keras model defined in model.py and trains on data loaded and
    preprocessed in data_pipeline.py. Saves the trained model in TensorFlow SavedModel
    format to the path defined in part by the --job-dir argument.

    Args:
    args: dictionary of arguments - see get_args() for details
    """
    # Config datasets for consistent usage
    ds_config = dict(window_size=args.window_size,
                     shift=args.shift,
                     stride=args.stride,
                     batch_size=args.batch_size)
    ds_train_path = args.data_dir_train
    ds_val_path = args.data_dir_validate

    # Calculate steps_per_epoch_train, steps_per_epoch_test
    # This is needed, since for counting repeat has to be false
    steps_per_epoch_train = calculate_steps_per_epoch(data_dir=ds_train_path, dataset_config=ds_config)
    
    steps_per_epoch_validate = calculate_steps_per_epoch(data_dir=ds_val_path, dataset_config=ds_config)
    
    # load datasets
    dataset_train = dp.create_dataset(data_dir=ds_train_path,
                                      window_size=ds_config["window_size"],
                                      shift=ds_config["shift"],
                                      stride=ds_config["stride"],
                                      batch_size=ds_config["batch_size"])
    
    dataset_validate = dp.create_dataset(data_dir=ds_val_path,
                                         window_size=ds_config["window_size"],
                                         shift=ds_config["shift"],
                                         stride=ds_config["stride"],
                                         batch_size=ds_config["batch_size"])

    # create model
    model = split_model.create_keras_model(window_size=args.window_size,
                                           loss=args.loss)

    run_timestr = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.tboard_dir is None:
        tboard_dir = os.path.join(cst.TENSORBOARD_DIR, run_timestr)
    else:
        tboard_dir = args.tboard_dir

    callbacks = [
        tf.keras.callbacks.TensorBoard(log_dir=tboard_dir,
                                       histogram_freq=0,
                                       ),
        CustomCheckpoints(log_dir=tboard_dir,
                          save_best_only=True,
                          start_epoch=args.save_from,
                          dataset_path=ds_val_path,
                          dataset_config=ds_config)
        ]

    # train model
    model.fit(
        dataset_train, 
        epochs=args.num_epochs,
        steps_per_epoch=steps_per_epoch_train,
        validation_data=dataset_validate,
        validation_steps=steps_per_epoch_validate,
        verbose=1,
        callbacks=callbacks)
    
    # save model from last epoch
    saved_model_dir = os.path.join(tboard_dir, "checkpoints", "last_epoch")
    tf.keras.experimental.export_saved_model(model, saved_model_dir)


def calculate_steps_per_epoch(data_dir, dataset_config):
    temp_dataset = dp.create_dataset(data_dir=data_dir,
                                     window_size=dataset_config["window_size"],
                                     shift=dataset_config["shift"],
                                     stride=dataset_config["stride"],
                                     batch_size=dataset_config["batch_size"],
                                     repeat=False)
    steps_per_epoch = 0
    for batch in temp_dataset:
        steps_per_epoch += 1
    return steps_per_epoch


if __name__ == '__main__':
    args = get_args()
    logging.set_verbosity(args.verbosity)
    train_and_evaluate(args)