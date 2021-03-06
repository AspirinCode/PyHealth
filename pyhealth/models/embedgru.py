import os
import torch
import torch.nn as nn
import pickle
import warnings
from ..utils.loss import callLoss
from .base import BaseControler

warnings.filterwarnings('ignore')

class callPredictor(nn.Module):
    def __init__(self, 
                 input_size = None,
                 embed_size = 16,
                 layer_hidden_sizes = [10,20,15],
                 num_layers = 3,
                 bias = True,
                 dropout = 0.5,
                 bidirectional = True,
                 batch_first = True,
                 label_size = 1):
        super(callPredictor, self).__init__()
        assert input_size != None and isinstance(input_size, int), 'fill in correct input_size' 
        self.num_layers = num_layers
        self.rnn_models = []
        self.input_size = input_size        
        self.embed_size = embed_size
        if bidirectional:
            layer_input_sizes = [embed_size] + [2 * chs for chs in layer_hidden_sizes]
        else:
            layer_input_sizes = [embed_size] + layer_hidden_sizes
        self.label_size = label_size
        self.output_size = layer_input_sizes[-1]

        self.embed_func = nn.Linear(self.input_size, self.embed_size)
        for i in range(num_layers):
            self.rnn_models.append(nn.GRU(input_size = layer_input_sizes[i],
                                     hidden_size = layer_hidden_sizes[i],
                                     num_layers = num_layers,
                                     bias = bias,
                                     dropout = dropout,
                                     bidirectional = bidirectional,
                                     batch_first = batch_first))
        self.output_func = nn.Linear(self.output_size, self.label_size)
            
    def forward(self, input_data):
        
        """
        
        Parameters
        
        ----------
        input_data = {
                      'X': shape (batchsize, n_timestep, n_featdim)
                      'M': shape (batchsize, n_timestep)
                      'cur_M': shape (batchsize, n_timestep)
                      'T': shape (batchsize, n_timestep)
                     }
        
        Return
        
        ----------
        
        all_output, shape (batchsize, n_timestep, n_labels)
            
            predict output of each time step
            
        cur_output, shape (batchsize, n_labels)
        
            predict output of last time step

        
        """
        
        X = input_data['X']
        M = input_data['M']
        cur_M = input_data['cur_M']
        batchsize, n_timestep, n_orifeatdim = X.shape
        _ori_X = X.view(-1, n_orifeatdim)
        _embed_X = self.embed_func(_ori_X)
        _data = _embed_X.reshape(batchsize, n_timestep, self.embed_size)
        for temp_rnn_model in self.rnn_models:
            _data, _ = temp_rnn_model(_data)
        outputs = _data
        all_output = outputs * M.unsqueeze(-1)
        n_batchsize, n_timestep, n_featdim = all_output.shape
        all_output = self.output_func(outputs.reshape(n_batchsize*n_timestep, n_featdim)).reshape(n_batchsize, n_timestep, self.label_size)
        cur_output = (all_output * cur_M.unsqueeze(-1)).sum(dim=1)
        return all_output, cur_output

class EmbedGRU(BaseControler):

    def __init__(self, 
                 expmodel_id = 'test.new', 
                 task = 'phenotyping',
                 n_epoch = 100,
                 n_batchsize = 5,
                 learn_ratio = 1e-4,
                 weight_decay = 1e-4,
                 n_epoch_saved = 1,
                 embed_size = 16,
                 layer_hidden_sizes = [10,20,15],
                 bias = True,
                 dropout = 0.5,
                 bidirectional = True,
                 batch_first = True,
                 loss_name = 'L1LossSigmoid',
                 target_repl = False,
                 target_repl_coef = 0.,
                 aggregate = 'sum',
                 optimizer_name = 'adam',
                 use_gpu = False
                 ):
        """
        On an healthcare data sequence, firstly embed original features into embeded feature space, 
            then applies a multi-layer Gated recurrent unit (GRU) RNN.


        Parameters

        ----------
        exp_id : str, optional (default='init.test') 
            name of current experiment
            
        task : str, optional (default='phenotyping')
            name of current healthcare task
            
        n_epoch : int, optional (default = 100)
            number of epochs with the initial learning rate
            
        n_batchsize : int, optional (default = 5)
            batch size for model training
   
        learn_ratio : float, optional (default = 1e-4)
            initial learning rate for adam
  
        weight_decay : float, optional (default = 1e-4)
            weight decay (L2 penalty)
  
        n_epoch_saved : int, optional (default = 1)
            frequency of saving checkpoints at the end of epochs
        
        embed_size: int, optional (default = 16)
            The number of the embeded features of original input
            
        layer_hidden_sizes : list, optional (default = [10,20,15])
            The number of features of the hidden state h of each layer
            
        bias : bool, optional (default = True)
            If False, then the layer does not use bias weights b_ih and b_hh. 
            
        dropout : float, optional (default = 0.5)
            If non-zero, introduces a Dropout layer on the outputs of each GRU layer except the last layer, 
            with dropout probability equal to dropout. 

        bidirectional : bool, optional (default = True)
            If True, becomes a bidirectional GRU. 
            
        batch_first : bool, optional (default = False)
            If True, then the input and output tensors are provided as (batch, seq, feature). 
             
        loss_name : str, optional (default='SigmoidCELoss') 
            Name or objective function.

        use_gpu : bool, optional (default=False) 
            If yes, use GPU recources; else use CPU recources 

        """
 
        super(EmbedGRU, self).__init__(expmodel_id)
        self.task = task
        self.n_batchsize = n_batchsize
        self.n_epoch = n_epoch
        self.learn_ratio = learn_ratio
        self.weight_decay = weight_decay
        self.n_epoch_saved = n_epoch_saved
        self.embed_size = embed_size
        self.layer_hidden_sizes = layer_hidden_sizes
        self.num_layers = len(layer_hidden_sizes)
        self.bias = bias
        self.dropout = dropout
        self.bidirectional = bidirectional
        self.batch_first = batch_first
        self.loss_name = loss_name
        self.target_repl = target_repl
        self.target_repl_coef = target_repl_coef
        self.aggregate = aggregate
        self.optimizer_name = optimizer_name
        self.use_gpu = use_gpu
        self._args_check()
        
    def _build_model(self):
        """
        
        Build the crucial components for model training 
 
        
        """
        
        _config = {
            'input_size': self.input_size,
            'embed_size': self.embed_size,
            'layer_hidden_sizes': self.layer_hidden_sizes,
            'num_layers': self.num_layers,
            'bias': self.bias,
            'dropout': self.dropout,
            'bidirectional': self.bidirectional,
            'batch_first': self.batch_first,
            'label_size': self.label_size
            }
        self.predictor = callPredictor(**_config).to(self.device)
        self.predictor= torch.nn.DataParallel(self.predictor)
        self._save_predictor_config(_config)
        self.criterion = callLoss(task = self.task,
                                  loss_name = self.loss_name,
                                  target_repl = self.target_repl,
                                  target_repl_coef = self.target_repl_coef,
                                  aggregate = self.aggregate)
        self.optimizer = self._get_optimizer()

    def fit(self, train_data, valid_data):
        
        """
        Parameters

        ----------

        train_data : {
                      'x':list[episode_file_path], 
                      'y':list[label], 
                      'l':list[seq_len], 
                      'feat_n': n of feature space, 
                      'label_n': n of label space
                      }

            The input train samples dict.
 
        valid_data : {
                      'x':list[episode_file_path], 
                      'y':list[label], 
                      'l':list[seq_len], 
                      'feat_n': n of feature space, 
                      'label_n': n of label space
                      }

            The input valid samples dict.


        Returns

        -------

        self : object

            Fitted estimator.

        """
        self.input_size = train_data['feat_n']
        self.label_size = train_data['label_n']

        self._build_model()

        train_reader = self._get_reader(train_data, 'train')
        valid_reader = self._get_reader(valid_data, 'valid')
        
        best_score = 1e5
        for epoch in range(0, self.n_epoch):
            print('\nEpoch: [{0}|{1}]'.format(epoch + 1, self.n_epoch))
            self._train(train_reader)
            self._valid(valid_reader)
            test_score = self.acc['valid'][-1]
            print ('Train Loss : {:.3f}, Valid Loss : {:.3f}'.format(self.acc['train'][-1], self.acc['valid'][-1]))
            unit = {'epoch': epoch,
                    'state_dict': self.predictor.state_dict(),
                    'score': test_score,
                    'best_score': best_score,
                    'optimizer' : self.optimizer.state_dict()}
            if test_score<best_score:
                best_score = test_score
                unit['best_score'] = best_score
                self._save_checkpoint(unit, epoch, is_best = True)
            if epoch%self.n_epoch_saved == 0:
                self._save_checkpoint(unit, epoch, is_best = False)
            self._save_checkpoint(unit, -1, is_best = False)

    def load_model(self, loaded_epoch = ''):
        """
        Parameters

        ----------

        loaded_epoch : str, loaded model name 
        
            we save the model by <epoch_count>.epoch, latest.epoch, best.epoch

        Returns

        -------

        self : object

            loaded estimator.

        """

        predictor_config = self._load_predictor_config()
        self.predictor = callPredictor(**predictor_config).to(self.device)
        if loaded_epoch != '':
            self._loaded_epoch = loaded_epoch
        else:
            self._loaded_epoch = 'best'
        load_checkpoint_path = os.path.join(self.checkout_dir, self._loaded_epoch + '.checkpoint.pth.tar')
        if os.path.exists(load_checkpoint_path):
            try:
                checkpoint = torch.load(load_checkpoint_path)
            except:
                checkpoint = torch.load(load_checkpoint_path, map_location = 'cpu')
            self.predictor.load_state_dict({key[7:]: value for key, value in checkpoint['state_dict'].items()})
            print ('load '+self._loaded_epoch+'-th epoch model')  
        else:
            print ('no exist '+self._loaded_epoch+'-th epoch model, please dbcheck in dir {0}'.format(self.checkout_dir))

    def get_results(self):
        
        """
        
        Load saved prediction results in current ExpID
            truth_value: proj_root/experiments_records/*****(exp_id)/results/y.xxx
            predict_value: proj_root/experiments_records/*****(exp_id)/results/hat_y.xxx
            xxx represents the loaded model
        
        """
        try:
            hat_y = pickle.load(open(os.path.join(self.result_dir, 'hat_y.'+self._loaded_epoch),'rb'))
        except IOError:
            print ('Error: cannot find file {0} or load failed'.format(os.path.join(self.result_dir, 'hat_y.'+self._loaded_epoch)))
        try:
            y = pickle.load(open(os.path.join(self.result_dir, 'y.'+self._loaded_epoch),'rb'))
        except IOError:
            print ('Error: cannot find file {0} or load failed'.format(os.path.join(self.result_dir, 'y.'+self._loaded_epoch)))

        results = {'hat_y': hat_y, 'y': y}
        
        return results
  
    def inference(self, test_data):
        """
        Parameters

        ----------

        test_data : {
                      'x':list[episode_file_path], 
                      'y':list[label], 
                      'l':list[seq_len], 
                      'feat_n': n of feature space, 
                      'label_n': n of label space
                      }

            The input test samples dict.
 
 
        """
        test_reader = self._get_reader(test_data, 'test')
        self._test(test_reader)
 

    def _args_check(self):
        """
        
        Check args whether valid/not and give tips
 
        
        """
        assert isinstance(self.task,str) and self.task in ['mortality','phenotyping'], \
            'fill in correct task (str, [\'mortality\',\'phenotyping\'])'
        assert isinstance(self.n_batchsize,int) and self.n_batchsize>0, \
            'fill in correct n_batchsize (int, >0)'
        assert isinstance(self.n_epoch,int) and self.n_epoch>0, \
            'fill in correct n_epoch (int, >0)'
        assert isinstance(self.learn_ratio,float) and self.learn_ratio>0., \
            'fill in correct learn_ratio (float, >0.)'
        assert isinstance(self.weight_decay,float) and self.weight_decay>=0., \
            'fill in correct weight_decay (float, >=0.)'
        assert isinstance(self.n_epoch_saved,int) and self.n_epoch_saved>0 and self.n_epoch_saved < self.n_epoch, \
            'fill in correct n_epoch (int, >0 and <{0}).format(self.n_epoch)'
        assert isinstance(self.embed_size,int) and self.embed_size>0, \
            'fill in correct embed_size (int, >0)'
        assert isinstance(self.layer_hidden_sizes,list) and len(self.layer_hidden_sizes)>0, \
            'fill in correct layer_hidden_sizes (list, such as [10,20,15])'
        assert isinstance(self.num_layers,int) and self.num_layers>0, \
            'fill in correct num_layers (int, >0)'
        assert isinstance(self.bias,bool), \
            'fill in correct bias (bool)'
        assert isinstance(self.dropout,float) and self.dropout>0. and self.dropout<1., \
            'fill in correct learn_ratio (float, >0 and <1.)'
        assert isinstance(self.bidirectional,bool), \
            'fill in correct bidirectional (bool)'
        assert isinstance(self.batch_first,bool), \
            'fill in correct batch_first (bool)'
        assert isinstance(self.target_repl,bool), \
            'fill in correct target_repl (bool)'
        assert isinstance(self.target_repl_coef,float) and self.target_repl_coef>=0. and self.target_repl_coef<=1., \
            'fill in correct target_repl_coef (float, >=0 and <=1.)'
        assert isinstance(self.aggregate,str) and self.aggregate in ['sum','avg'], \
            'fill in correct aggregate (str, [\'sum\',\'avg\'])'
        assert isinstance(self.optimizer_name,str) and self.optimizer_name in ['adam'], \
            'fill in correct optimizer_name (str, [\'adam\'])'
        assert isinstance(self.use_gpu,bool), \
            'fill in correct use_gpu (bool)'
        assert isinstance(self.loss_name,str), \
            'fill in correct optimizer_name (str)'

        self.loss_name = self._get_lossname(self.loss_name)
        self.device = self._get_device()
