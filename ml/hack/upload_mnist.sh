#!/bin/bash

KUBEML_HOME="../pkg/kubeml-cli/kubeml"
MNIST_DATASET="../experiments/datasets/mnist"

echo "Uploading MNIST dataset"

"$KUBEML_HOME" dataset create --name mnist --traindata ${MNIST_DATASET}/mnist_x_train.npy \
                                        --trainlabels ${MNIST_DATASET}/mnist_y_train.npy \
                                        --testdata ${MNIST_DATASET}/mnist_x_test.npy \
                                        --testlabels ${MNIST_DATASET}/mnist_y_test.npy