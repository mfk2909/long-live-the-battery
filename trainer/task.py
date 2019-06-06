import argparse
import os
from absl import logging
import time

import tensorflow as tf
from tensorflow.keras.callbacks import TensorBoard

import data_pipeline as dp
import split_model

TRAINED_MODEL_DIR_LOCAL = './'
TFRECORDS_DIR_LOCAL = 'data/tfrecords/train/*tfrecord'
TB_LOG_DIR_LOCAL = 'Graph'


def get_args():
    """Argument parser.

    Returns:
        Dictionary of arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--job-dir',
        type=str,
        default=TRAINED_MODEL_DIR_LOCAL,
        help='local or GCS location for writing checkpoints and exporting models')
    parser.add_argument(
        '--tfrecords-dir',
        type=str,
        default=TFRECORDS_DIR_LOCAL,
        help='local or GCS location for reading TFRecord files')
    parser.add_argument(
        '--tboard-dir',
        type=str,
        default=TB_LOG_DIR_LOCAL,
        help='local or GCS location for reading TFRecord files')
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
        default=100,
        type=int,
        help='window size for sliding window in training sample generation, default=100')
    parser.add_argument(
        '--shift',
        default=20,
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
    # calculate steps_per_epoch
    temp_dataset = dp.create_dataset(
                        data_dir=args.tfrecords_dir, 
                        window_size=args.window_size,
                        shift=args.shift,
                        stride=args.stride,
                        batch_size=args.batch_size,
                        repeat=False)
    steps_per_epoch = 0
    for batch in temp_dataset:
        steps_per_epoch += 1
    
    # load dataset
    dataset = dp.create_dataset(
                        data_dir=args.tfrecords_dir, 
                        window_size=args.window_size,
                        shift=args.shift,
                        stride=args.stride,
                        batch_size=args.batch_size)

    # create model
    model = split_model.create_keras_model(args)
    
    # tensorboard callback
    tensorboard_log = TensorBoard(
                        log_dir=args.tboard_dir, 
                        histogram_freq=0,
                        write_graph=True, 
                        write_images=True)

    # train model
    model.fit(
        dataset, 
        epochs=args.num_epochs,
        steps_per_epoch=steps_per_epoch,
        verbose=1,
        callbacks=[tensorboard_log])
    
    # export saved model
    saved_model_path = os.path.join(args.job_dir, "saved_models/{}".format(int(time.time())))  
    tf.keras.experimental.export_saved_model(model, saved_model_path)
    print('Model should export to: ', saved_model_path)


if __name__ == '__main__':
    args = get_args()
    logging.set_verbosity(args.verbosity)
    train_and_evaluate(args)