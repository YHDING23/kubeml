from abc import ABC
from collections import defaultdict
from typing import Dict, Tuple, Any, Union, Callable, Iterable, Sequence

import flask
import numpy as np
import pickle
import redisai as rai
import requests
from flask import request, jsonify, current_app
from redis.exceptions import RedisError
from torch.utils.data import DataLoader
from datetime import datetime

from .dataset import _KubeArgs, KubeDataset
from .exceptions import *
from .util import *
import os

# Load from environment the values from th MONGO IP and PORT
try:
    REDIS_URL = os.environ['REDIS_URL']
    REDIS_PORT = os.environ['REDIS_PORT']
    logging.debug(f'Found configuration for redis {REDIS_URL}:{REDIS_PORT}')
except KeyError:
    logging.debug("Could not find redis configuration in env, using defaults")
    REDIS_URL = "redisai.kubeml"
    REDIS_PORT = 6379


class KubeModel(ABC):

    def __init__(self, network: nn.Module, dataset: KubeDataset, gpu=False):
        """Init the KubeModel, device can be either gpu or cpu"""

        # if device is set to gpu, get the correct gpu if
        # for the
        self._network = network
        self._dataset = dataset
        self.platform = 'gpu' if gpu else 'cpu'
        self.device = None
        self.args = None
        self.logger = None

        # training options, these will be updated when reading the parameters
        # in each iteration
        self.lr = None
        self.batch_size = None
        self.task = None
        self.optimizer = None
        self.epoch = None

        # initialize redis connection
        self._redis_client = rai.Client(host=REDIS_URL, port=REDIS_PORT)

    # allow to call the network from the kubemodel
    def __call__(self, *args, **kwargs):
        """
        Call allows users to invoke the underlying torch module by using the
        KubeModel. An example of this is when perrforming the forward pass:

        output = self(x)

        That the users can call in the train method

        :param args:
        :param kwargs:
        :return: the output of the network
        """
        return self._network(*args, **kwargs)

    # Functions that act like a proxy to the same
    # methods of the underlying pytorch module for convenience
    def parameters(self):
        """
        Parameters returns the parameter generator of the underlying network

        :return: The parameter generator of the torch module
        """
        return self._network.parameters()

    def apply(self, fn: Callable[[nn.Module], None]):
        """
        apply acts as a proxy to the underlying pytorch modules'
        apply method. It is mainly used to initialize weights of the network

        :param fn: function that initializes the layer weights
        :return: self
        """
        self._network.apply(fn)
        return self

    def _read_args(self):
        """Parse the args and update the parameters"""
        self.args = _KubeArgs.parse()
        self.lr = self.args.lr
        self.batch_size = self.args.batch_size
        self.task = self.args._task
        self.epoch = self.args.epoch

    def _config_optimizer(self):
        """
        Configures the optimizer specified by the user to be used in training. By default
        it resets the state of the optimizer like the momentum buffer in SGD with momentum
        :return:
        """
        optimizer = self.configure_optimizers()
        self.optimizer = optimizer
        self.logger.debug(f"Configure optimizer at epoch: {self.epoch}")



    def _load_optimizer_state(self):
        """
        Saves the optimizer state to host machine path /output and it will be loaded again in
        the following epochs
        """
        job_id = self.args._job_id
        func_id = self.args._func_id
        if os.path.isfile(f'/output/{job_id}:opt:{func_id}.pkl'):
            self.logger.debug(f'loading optimizer file from host, path: /output/{job_id}:opt:{func_id}.pkl')
            with open(f'/output/{job_id}:opt:{func_id}.pkl', 'rb') as f:
                self.logger.debug(f'loading optimizer file, epoch: {self.epoch}')
                state = pickle.load(f)
                self.optimizer.load_state_dict(state)
                self.logger.debug('optimizer file loaded')
                print('optimizer file loaded') 

    def _reset_optimizer_state(self):
        """
        Resets the optimizer state. Called at the start of each iteration
        since the momentum or other factors of the optimizer can hinder progress if
        carried over from another model
        """
        if self.optimizer is not None:
            self.optimizer.state = defaultdict(dict)


    def _save_optimizer_state(self):
        """
        Saves the optimizer state to host machine path /output and it will be loaded again in
        the following epochs
        """
        job_id = self.args._job_id
        func_id = self.args._func_id
        self.logger.debug('saving optimizer file to host')

        with open(f'/output/{job_id}:opt:{func_id}.pkl', 'wb') as f:
            self.logger.debug(f'saving optimizer file to path: /output/{job_id}:opt:{func_id}.pkl , epoch: {self.epoch}')
            pickle.dump(self.optimizer.state_dict(), f)
            self.logger.debug('optimizer file saved')
        print('optimizer file saved')   

    def _save_file_test(self):
        """
        Saves the test file to be loaded again in
        the following epochs
        """
        job_id = self.args._job_id
        func_id = self.args._func_id
        self.logger.debug('saving test file to host')

        with open(f'/output/{job_id}:test:{func_id}.pkl', 'wb') as f:
            self.logger.debug(f'saving test file to path: /output/{job_id}:test:{func_id}.pkl , epoch: {self.epoch}')
            pickle.dump('test', f)
            self.logger.debug('test file saved')
        print('test file saved')   

    def _load_file_test(self):
        """
        Saves the file state to be loaded again in
        the following epochs
        """
        job_id = self.args._job_id
        func_id = self.args._func_id
        if os.path.isfile(f'/output/{job_id}:test:{func_id}.pkl'):
            self.logger.debug(f'loading test file from host, path: /output/{job_id}:test:{func_id}.pkl')
            with open(f'/output/{job_id}:test:{func_id}.pkl', 'rb') as f:
                self.logger.debug(f'loading test file, epoch: {self.epoch}')
                state = pickle.load(f)
                self.logger.debug(f'file loaded, value: {state}')
                print('file loaded')    

    def _save_optimizer_redis(self):
        """
        Saves the optimizer to the redis
        """
        job_id = self.args._job_id
        func_id = self.args._func_id

        self.logger.debug("Saving optimizer to the redis")

        weight_key = f'{job_id}:optimizer:{func_id}' 
        pickled_val  = pickle.dumps(self.optimizer.state_dict())

        #self.logger.debug(f"Saving optimizer value: {pickled_val}")

        self._redis_client.set(weight_key, pickled_val)


    def _load_optimizer_redis(self):
        """
        Checks for a previously saved state of the optimizer and loads it from redis
        """

        job_id = self.args._job_id
        func_id = self.args._func_id

        self.logger.debug("Load optimizer from the redis")

        weight_key = f'{job_id}:optimizer:{func_id}' 

        if  self._redis_client.exists(weight_key):
            # used for testing redis
            # epoch = self.args.epoch
            # weight_key_print = f'{job_id}:print:{func_id}' 
            # value = f'{job_id}:print:{func_id}/{epoch}' 
            # self._redis_client.set(weight_key_print, value)

            pickled_val = self._redis_client.get(weight_key)
            opt_states = pickle.loads(pickled_val)  
            self.optimizer.load_state_dict(opt_states)



    def _save_redis_test(self):
        """
        Saves the optimizer to the redis
        """
        job_id = self.args._job_id
        func_id = self.args._func_id
        epoch = self.args.epoch

        self.logger.debug("Saving test case to the redis")

        weight_key = f'{job_id}:test:{func_id}' 
        value = f'{job_id}:test:{func_id}/{epoch}' 
        self._redis_client.set(weight_key, value)


    def _load_redis_test(self):
        """
        Checks for a previously saved state of the optimizer and loads it from redis
        """

        job_id = self.args._job_id
        func_id = self.args._func_id

        self.logger.debug("Load test case from the redis")

        weight_key = f'{job_id}:test:{func_id}' 
        pickled_val = self._redis_client.get(weight_key)
        self.logger.debug(f"loading value:  {pickled_val}")


    def _get_logger(self):
        """
        Sets the current logger within the network according to the flask context
        """
        self.logger = current_app.logger

    def start(self) -> Tuple[flask.Response, int]:
        """
        Start executes the function invoked by the user
        """
        # parse arguments and
        self._read_args()
        self._get_logger()

        if self.task == "init":
            layers = self.__initialize()
            return jsonify(layers), 200

        elif self.task == "train":
            loss = self.__train()
            return jsonify(loss=loss), 200

        elif self.task == "val":
            acc, loss, length = self.__validate()
            return jsonify(loss=loss, accuracy=acc, length=length), 200

        elif self.task == "infer":
            preds = self.__infer()
            return jsonify(predictions=preds), 200

        else:
            self._redis_client.close()
            raise KubeMLException(f"Task {self.task} not recognized", 400)

    def __initialize(self) -> List[str]:
        """
        Initializes the network

        :return: the names of the optimizable layers of the network, which will be saved in the reference model
        """
        try:
            self.init()
            # according to the comments, it is nothing to do with the training process.
            self.__save_model()

        except RedisError as re:
            raise StorageError(re)
        finally:
            self._redis_client.close()

        return [name for name in self._network.state_dict()]

    def _on_train_start(self):
        """
        Prepares the network for training
        :return:
        """
        self._set_device()
        self._network.train()
        self._config_optimizer()


    def _on_train_end(self):
        """
        Executed after the end of the training loop
        :return:
        """
        # self.__save_model()
        # self._save_optimizer_redis()
        pass
        # self._save_optimizer_state()

    def _on_iteration_start(self):
        """
        Called at the start of each iteration

        Load the model from the averaged storage, and
        reset optimizer state
        :return:
        """
        startModelLoading = datetime.now()
        self.__load_model()
        self.logger.debug(f"Model loading Time, {datetime.now() - startModelLoading}")
        startOptimizerLoading = datetime.now()
        self._load_optimizer_redis()
        self.logger.debug(f"Optimizer loading Time, {datetime.now() - startOptimizerLoading}")

        # self._load_redis_test()
        # self._load_file_test()
        # self._load_optimizer_state()

        #self._load_optimizer_state()

        #self._reset_optimizer_state()

    def _on_iteration_end(self):
        """
        Called at the end of each iteration
        :return:
        """
        startModelSaving = datetime.now()
        self.__save_model()
        self.logger.debug(f"Model saving Time, {datetime.now() - startModelSaving}")
        startoptimizerSaving = datetime.now()
        self._save_optimizer_redis()
        self.logger.debug(f"Optimizer saving Time, {datetime.now() - startoptimizerSaving}")
        # self._save_redis_test()
        # self._save_file_test()
        # self._save_optimizer_state()

    def _batch_to_device(self, batch: Union[torch.Tensor, Iterable[torch.Tensor]]):
        """
        Moves the batch fetched from the dataloader to the in use device. This allows to
        return an unbounded number of tensors from the dataset and passing those in the train step

        :return: The batch variables moved to the device
        """

        # this can be a list, tuple or tensor if it is just one
        elem_type = type(batch)

        # if it is a tensor, return to device directly
        if isinstance(batch, torch.Tensor):
            return batch.to(self.device)

        # if it is a tuple, return a tuple
        # with the elements to the device
        elif isinstance(batch, tuple):
            return elem_type((elem.to(self.device) for elem in batch))

        elif isinstance(batch, Sequence):
            return elem_type([elem.to(self.device) for elem in batch])

        else:
            return batch

    def __train(self) -> float:
        """
        Function called to train the network. Loads the reference model from the database,
        trains with the method provided by the user and saves the model after training to the database

        :return: The loss of the epoch, as returned by the user function
        """
        self.logger.debug(f"----------------------Epoch: {self.epoch} Starts---------------------------")
        self._on_train_start()



        # Determine the batches that we need to train on and the first subset id
        assigned_subsets = split_minibatches(range(self._dataset.num_docs),
                                             self.args._N)[self.args._func_id]

        # calculate the number of subsets that we need to train on
        # per epoch

        startDataLPrepartion = datetime.now()

        subsets_per_iter = get_subset_period(self.args._K,
                                             self.args.batch_size,
                                             assigned_subsets)
        self.logger.debug(f"Subsets per iteration: {subsets_per_iter}")
        intervals = range(assigned_subsets.start, assigned_subsets.stop, subsets_per_iter)

        self.logger.debug(f"Data prepartion Time, {datetime.now() - startDataLPrepartion}")

        # the loss will be added cross intervals, each interval will have one loader, whose length
        # will determine the number of losses added.
        loss = 0
        num_iterations = 0
        for i in intervals:

            startDataLoading = datetime.now()

            self.logger.debug(f"Starting iteration {i}")
            self._dataset._load_train_data(start=i, end=min(assigned_subsets.stop, i + subsets_per_iter))

            # create the loader that will be used
            loader = DataLoader(self._dataset, batch_size=self.batch_size, shuffle= True)
            num_iterations += len(loader)

            self.logger.debug(f"Data Loading Time, {datetime.now() - startDataLoading}")

            # load the reference model, train and save
            try:
                self._on_iteration_start()

                startTraining = datetime.now()

                for idx, batch in enumerate(loader):
                    # send the batch to the appropriate device
                    batch = self._batch_to_device(batch)
                    loss += self.train(batch, idx)
                    # self.logger.debug(f'loss is {loss}, iterations are {num_iterations}')

                self.logger.debug(f"Training Time, {datetime.now() - startTraining}")

                self._on_iteration_end()
            except RedisError as re:
                raise StorageError(re)
            finally:
                self._redis_client.close()

            # send notification to the train job to refresh the model if not
            # the last interval
            if i != intervals[-1]:
                self.__send_finish_signal()

        self._on_train_end()

        return loss / num_iterations

    def _on_validation_start(self):
        """
        Executed before the validation
        :return:
        """
        self._set_device()
        self._network.eval()

    def __validate(self):
        """
        Validate sets the device to be used and sets the network in eval mode.
        Then it:
        - Loads the validation data
        - Creates a data loader
        - Feeds the validate function defined by the user with datapoints already sent to the correct device

        :return: A tuple containing the mean accuracy and loss on the val dataset and the number or datapoints
        """

        self._on_validation_start()

        # Determine the batches that we need to validate on and the first
        # subset id that we need to get each iteration
        assigned_subsets = split_minibatches(range(self._dataset.num_val_docs), self.args._N)[self.args._func_id]

        # load the validation data
        self._dataset._load_validation_data(start=assigned_subsets.start,
                                            end=assigned_subsets.stop)

        # create the loader that will be used
        loader = DataLoader(self._dataset, batch_size=self.batch_size)

        acc, loss = 0, 0
        try:
            self.__load_model()
            with torch.no_grad():
                for idx, batch in enumerate(loader):
                    batch = self._batch_to_device(batch)
                    _acc, _loss = self.validate(batch, idx)

                    # accumulate statistics
                    acc += _acc
                    loss += _loss
        except RedisError as re:
            raise StorageError(re)
        finally:
            self._redis_client.close()

        return acc / len(loader), loss / len(loader), len(self._dataset)

    def __infer(self) -> Union[torch.Tensor, np.ndarray, List[float]]:
        data_json = request.json
        if not data_json:
            self.logger.error("JSON not found in request")
            raise DataError

        preds = self.infer(self._network, data_json)

        if isinstance(preds, torch.Tensor):
            return preds.cpu().numpy().tolist()
        elif isinstance(preds, np.ndarray):
            return preds.tolist()
        elif isinstance(preds, list):
            return preds
        else:
            raise InvalidFormatError

    def _set_device(self):
        """Set device updates the used gpu or cpu based on the function id and the previously
        used devices"""

        # if the indicated platform is cpu,
        # set that as the torch device
        if self.platform == 'cpu':
            self.device = torch.device('cpu')

        # if it's gpu, get the appropriate one and put the model there
        else:
            gpu_id = get_gpu(self.args._func_id)
            self.device = torch.device(f'cuda:{gpu_id}')
            self._network = self._network.to(self.device)
            self.logger.debug(f'Set device to {self.device}')

    def __send_finish_signal(self):
        """Sends a request to the train job communicating that the iteration is over
        and the model is published in the database.

        The PS will not respond until all the functions have finished the step
        """

        # create the url for the job service
        url = f"http://job-{self.args._job_id}.kubeml/next/{self.args._func_id}"

        try:
            self.logger.debug(f"Sending request to {url}")
            resp = requests.post(url)
        except requests.ConnectionError as e:
            self.logger.error("error connecting to the train job")
            raise MergeError(e)

        if not resp.ok:
            self.logger.error(f"Received non OK message. Code:{resp.status_code}. Msg: {resp.content.decode()}")
            raise MergeError()

    def __load_model(self):
        """
        Loads the model from redis ai and applies it to the network
        """
        state_dict = self.__get_model_dict()
        self._network.load_state_dict(state_dict)
        self.logger.debug("Loaded state dict from redis")

    def __get_model_dict(self) -> Dict[str, torch.Tensor]:
        """
        Fetches the model weights from the tensor storage

        :return: The state dict of the reference model
        """
        job_id = self.args._job_id

        state = dict()
        for name in self._network.state_dict():
            # load each of the layers in the statedict
            weight_key = f'{job_id}:{name}'
            w = self._redis_client.tensorget(weight_key)
            # set the weight
            state[weight_key[len(job_id) + 1:]] = torch.from_numpy(w)

        #self.logger.debug(f'Layers are {state.keys()}')

        return state

    def __save_model(self):
        """
        Saves the model to the tensor storage
        """
        job_id = self.args._job_id
        task = self.args._task
        func_id = self.args._func_id

        self.logger.debug("Saving model to the database")
        with torch.no_grad():
            for name, layer in self._network.state_dict().items():
                # Save the weights
                weight_key = f'{job_id}:{name}' \
                    if task == 'init' \
                    else f'{job_id}:{name}/{func_id}'
                self._redis_client.tensorset(weight_key, layer.cpu().detach().numpy(), dtype='float32')

        self.logger.debug('Saved model to the database')

    def configure_optimizers(self) -> torch.optim.Optimizer:
        pass

    def init(self):
        pass

    def train(self, batch, batch_index: int) -> float:
        pass

    def validate(self, batch, batch_index: int) -> Tuple[float, float]:
        pass

    def infer(self, data: List[Any]) -> Union[torch.Tensor, np.ndarray, List[float]]:
        pass