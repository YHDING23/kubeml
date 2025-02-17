import multiprocessing as mp
import time
import pandas as pd
import glob
import numpy as np
import pickle
import matplotlib.pyplot as plt
import os
import argparse

import torch
import torch.nn as nn
from torchvision import datasets
from torchvision import transforms
from torchvision import models
import torch.utils.data as tdata
from torch.nn.functional import nll_loss, cross_entropy

from torch import optim
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")


def task():
    print('Sleeping for 0.5 seconds')
    time.sleep(0.5)
    print('Finished sleeping')

def train(model: nn.Module, device,
          train_loader: tdata.DataLoader, epoch, i) -> float:
    """Loop used to train the network"""

    # create optimizer
    optimizer = optim.SGD(model.parameters(), lr=0.1, weight_decay=1e-4, momentum=0.9)
    if epoch > 0:
        load_state(optimizer, i)
        # load the global averaged model
        # model.load_state_dict(torch.load(f"model_resnet34.pt"))

    criterion = nn.CrossEntropyLoss()
    
    model.train()
    loss, tot = 0, 0
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)


        loss = cross_entropy(output, target)
        tot += loss.item()
        

        loss.backward()
        optimizer.step()
        

        if batch_idx % 30 == 0:
            print('Process: {}, Train Epoch: {} Step: [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                i, epoch, batch_idx, len(train_loader.batch_sampler),
                   100. * batch_idx / len(train_loader), loss.item()))
            

    # save the optimizer state
    save_state(optimizer, i)
    torch.save(model.state_dict(), f"model_{i}.pt")

    return tot/len(train_loader)


def validate(model, device, val_loader: tdata.DataLoader) -> (float, float):
    """Loop used to validate the network"""

    criterion =nn.CrossEntropyLoss()
    model.eval()
    model.to(device)
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in val_loader:
            #print(data.dtype, type(target))
            data, target = data.to(device), target.to(device)
            output = model(data)
            _, predicted = torch.max(output.data, 1)
            test_loss += cross_entropy(output, target).item()  # sum up batch loss
            correct += predicted.eq(target).sum().item()

    test_loss /= len(val_loader)

    accuracy = 100. * correct / len(val_loader.dataset)
    print('Test set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)'.format(
        test_loss, correct, len(val_loader.dataset),
        100. * correct / len(val_loader.dataset)))
    return accuracy, test_loss

def save_state(optimizer, i):
    with open(f'SGD-mini-batch-resnet34_{i}.pkl', 'wb') as f:
        #print(optimizer.state_dict()['state'])
        pickle.dump(optimizer.state_dict(), f)
    #print('saving optimizer state done, time is: ', datetime.now() - start)
def load_state(optimizer, i):
    if os.path.isfile(f'SGD-mini-batch-resnet34_{i}.pkl'):
        with open(f'SGD-mini-batch-resnet34_{i}.pkl', 'rb') as f:
            state = pickle.load(f)
            optimizer.load_state_dict(state)
            #update_state(optimizer, state)
        #print('loading optimizer state done, time is: ', datetime.now() - start)
    else:
        print('no state found')

def model_weight_average():
    model = models.resnet34(pretrained= False)
    cur_model = models.resnet34(pretrained= False)

    sd_avg = model.state_dict()

    beta = 1.0/parallelism 
    for i in range(parallelism):
        cur_model.load_state_dict(torch.load(f"./model_{i}.pt"))
        for key in cur_model.state_dict():
            if i == 0:
                sd_avg[key] = (cur_model.state_dict()[key]) / parallelism
            else:
                sd_avg[key] += (cur_model.state_dict()[key]) / parallelism
    model.load_state_dict(sd_avg)
    return model
    


if __name__ == "__main__": 
    parser = argparse.ArgumentParser()
    parser.add_argument('-b','--batch_size', default=128, type=int, metavar='N',
                        help='batch size')
    parser.add_argument('-lr','--learning_rate', default=0.1, type=float, metavar='N',
                        help='learning rate')                    
    parser.add_argument('-p', '--parallelism', default=2, type=int,
                        help='number of functions')                        
    parser.add_argument('-e','--epochs', default=10, type=int, metavar='N',
                        help='number of total epochs to run')
    args = parser.parse_args()
    torch.manual_seed(42) 
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(device)
    transform = transforms.Compose(
        [transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

    trainset = datasets.CIFAR10(root='./data', train=True,
                                            download=True, transform=transform)

    valset = datasets.CIFAR10(root='./data', train=False,
                                        download=True, transform=transform)
    val_loader= torch.utils.data.DataLoader(valset, batch_size=args.batch_size)
    #train_loader = torch.utils.data.DataLoader(trainset, batch_size=256)

    # May have to create our own dataloader
    # cifar10_x_train = trainset.data
    # cifar10_y_train = trainset.targets
    # cifar10_x_test = valset.data
    # cifar10_y_test = valset.targets

    # print(len(cifar10_x_train))
    # print(len(cifar10_y_train))
    # print(len(cifar10_x_test))
    # print(len(cifar10_y_test))




    start_time = time.perf_counter()
    torch.multiprocessing.set_start_method('spawn')#

    start = datetime.now()
    parallelism = args.parallelism
    for epoch in range(args.epochs):
        print('\nEpoch', epoch)
        processes = []

        model = models.resnet34(pretrained= False)

        # each process fetch and update its model,
        if epoch > 0:
            # update model: model average
            model = model_weight_average()
        model.to(device)
        # create optimizer

        for i in range(parallelism):
            # not sure it will re-gernate the data or not.
            train_sampler = torch.utils.data.distributed.DistributedSampler(trainset,num_replicas=parallelism, rank=i)
            train_loader = torch.utils.data.DataLoader(dataset=trainset,
                                            batch_size=args.batch_size,
                                            shuffle=False,
                                            num_workers=0,
                                            pin_memory=True,
                                            sampler=train_sampler)

            # print("Process ", i, dir(train_sampler), train_sampler.total_size)
            # print("Process ", i, dir(train_loader), type(train_loader.batch_sampler))
            # print("Process ", i, list(train_loader.batch_sampler))

            # for i, batch_indices in enumerate(train_loader.batch_sampler):
            #     print(f'Batch #{i} indices: ', batch_indices)

            p = mp.Process(target = train, args=(model, device, train_loader, epoch, i))
            # p = multiprocessing.Process(target = task)

            p.start()
            processes.append(p)
   
        # Joins all the processes 
        for p in processes:
            p.join()
        
        for p in processes:
            p.terminate()

     
        validation_start = datetime.now()
        validate(model, device, val_loader)
        print('Validiation done, time is: ', datetime.now() - validation_start)




    print("Training Completion Time: ", datetime.now() - start)
 


    finish_time = time.perf_counter()
 
    print(f"Program finished in {finish_time-start_time} seconds")
