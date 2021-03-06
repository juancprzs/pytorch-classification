import math
import torch
import torch.nn as nn
import torch.nn.functional as F

class BasicBlock(nn.Module):
    def __init__(self, in_planes, out_planes, kernel_size):
        super(BasicBlock, self).__init__()
        padding = (kernel_size-1) // 2
        self.layers = nn.Sequential()
        self.layers.add_module('Conv', nn.Conv2d(in_planes, out_planes, \
            kernel_size=kernel_size, stride=1, padding=padding, bias=False))
        self.layers.add_module('BatchNorm', nn.BatchNorm2d(out_planes))
        self.layers.add_module('ReLU',      nn.ReLU(inplace=True))

    def forward(self, x):
        return self.layers(x)

        feat = F.avg_pool2d(feat, feat.size(3)).view(-1, self.nChannels)
        
class EncBlock(nn.Module):
    def __init__(self, in_planes, out_planes, kernel_size):
        super(EncBlock, self).__init__()
        padding = (kernel_size-1) // 2
        self.layers = nn.Sequential()
        self.layers.add_module('Conv', nn.Conv2d(in_planes, out_planes, \
            kernel_size=kernel_size, stride=1, padding=padding, bias=False))
        self.layers.add_module('BatchNorm', nn.BatchNorm2d(out_planes))

    def forward(self, x):
        out = self.layers(x)
        return torch.cat([x,out], dim=1)


class GlobalAveragePooling(nn.Module):
    def __init__(self):
        super(GlobalAveragePooling, self).__init__()

    def forward(self, feat):
        num_channels = feat.size(1)
        return F.avg_pool2d(feat, (feat.size(2), feat.size(3))).view(-1, num_channels)

class NetworkInNetwork(nn.Module):
    def __init__(self, _num_inchannels=3, _num_stages=3, _use_avg_on_conv3=True):
        super(NetworkInNetwork, self).__init__()

        num_inchannels = _num_inchannels
        num_stages = _num_stages
        use_avg_on_conv3 = _use_avg_on_conv3

        assert(num_stages >= 3)
        nChannels  = 192
        nChannels2 = 160
        nChannels3 = 96

        # Auxiliary batch norms
        self.bn_nat = nn.BatchNorm2d(num_features=3)
        self.bn_adv = nn.BatchNorm2d(num_features=3)

        blocks = [nn.Sequential() for i in range(num_stages)]
        # 1st block
        blocks[0].add_module('Block1_ConvB1', BasicBlock(num_inchannels, nChannels, 5))
        blocks[0].add_module('Block1_ConvB2', BasicBlock(nChannels,  nChannels2, 1))
        blocks[0].add_module('Block1_ConvB3', BasicBlock(nChannels2, nChannels3, 1))
        blocks[0].add_module('Block1_MaxPool', nn.MaxPool2d(kernel_size=3,stride=2,padding=1))

        # 2nd block
        blocks[1].add_module('Block2_ConvB1',  BasicBlock(nChannels3, nChannels, 5))
        blocks[1].add_module('Block2_ConvB2',  BasicBlock(nChannels,  nChannels, 1))
        blocks[1].add_module('Block2_ConvB3',  BasicBlock(nChannels,  nChannels, 1))
        blocks[1].add_module('Block2_AvgPool', nn.AvgPool2d(kernel_size=3,stride=2,padding=1))
        blocks[1].add_module('Block2_Encode',  EncBlock(nChannels, nChannels, 1))

        # 3rd block
        blocks[2].add_module('Block3_ConvB1',  BasicBlock(nChannels, nChannels, 3))
        blocks[2].add_module('Block3_ConvB2',  BasicBlock(nChannels, nChannels, 1))
        blocks[2].add_module('Block3_ConvB3',  BasicBlock(nChannels, nChannels, 1))

        if num_stages > 3 and use_avg_on_conv3:
            blocks[2].add_module('Block3_AvgPool', nn.AvgPool2d(kernel_size=3,stride=2,padding=1))
        for s in range(3, num_stages):
            blocks[s].add_module('Block'+str(s+1)+'_ConvB1',  BasicBlock(nChannels, nChannels, 3))
            blocks[s].add_module('Block'+str(s+1)+'_ConvB2',  BasicBlock(nChannels, nChannels, 1))
            blocks[s].add_module('Block'+str(s+1)+'_ConvB3',  BasicBlock(nChannels, nChannels, 1))

        # global average pooling and classifier
        blocks.append(nn.Sequential())
        blocks[-1].add_module('GlobalAveragePooling',  GlobalAveragePooling())

        self._feature_blocks = nn.ModuleList(blocks)
        self.all_feat_names = ['conv'+str(s+1) for s in range(num_stages)] + ['classifier',]
        assert(len(self.all_feat_names) == len(self._feature_blocks))
        
        self.weight_initialization()

    def _parse_out_keys_arg(self, out_feat_keys):

        # By default return the features of the last layer / module.
        out_feat_keys = [self.all_feat_names[-1],] if out_feat_keys is None else out_feat_keys

        if len(out_feat_keys) == 0:
            raise ValueError('Empty list of output feature keys.')
        for f, key in enumerate(out_feat_keys):
            if key not in self.all_feat_names:
                raise ValueError('Feature with name {0} does not exist. Existing features: {1}.'.format(key, self.all_feat_names))
            elif key in out_feat_keys[:f]:
                raise ValueError('Duplicate output feature key: {0}.'.format(key))

        # Find the highest output feature in `out_feat_keys
        max_out_feat = max([self.all_feat_names.index(key) for key in out_feat_keys])

        return out_feat_keys, max_out_feat

    def forward(self, x, im_type, out_feat_keys=None):
        """Forward an image `x` through the network and return the asked output features.

        Args:
          x: input image.
          out_feat_keys: a list/tuple with the feature names of the features
                that the function should return. By default the last feature of
                the network is returned.

        Return:
            out_feats: If multiple output features were asked then `out_feats`
                is a list with the asked output features placed in the same
                order as in `out_feat_keys`. If a single output feature was
                asked then `out_feats` is that output feature (and not a list).
        """
        out_feat_keys, max_out_feat = self._parse_out_keys_arg(out_feat_keys)
        out_feats = [None] * len(out_feat_keys)

        feat = self.aux_bn(x, im_type=im_type) # auxiliary batch norm # x
        #encode
        for f in range(2):
            feat = self._feature_blocks[f](feat)
            key = self.all_feat_names[f]
            if key in out_feat_keys:
                out_feats[out_feat_keys.index(key)] = feat
    
        #reparameterize
        mu = feat[:,:192]
        logvar = feat[:, 192:]
        std = torch.exp(0.5*logvar)
        eps = torch.randn_like(std)
        feat = eps.mul(std * 0.001).add_(mu)
      
        #decode
        for f in range(2, max_out_feat+1):
            feat = self._feature_blocks[f](feat)
            key = self.all_feat_names[f]
            if key in out_feat_keys:
                out_feats[out_feat_keys.index(key)] = feat

        out_feats = out_feats[0] if len(out_feats)==1 else out_feats
        return out_feats

    def aux_bn(self, x, im_type):
        # Assertion
        possible_types = ['nat', 'adv']
        assert im_type in possible_types, 'im_type must be in ' + possible_types
        # Define batch norm layer to use
        if im_type == 'nat':
            batch_norm = self.bn_nat
        else:
            batch_norm =  self.bn_adv
        return batch_norm(x)


    def weight_initialization(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                if m.weight.requires_grad:
                    n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                    m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                if m.weight.requires_grad:
                    m.weight.data.fill_(1)
                if m.bias.requires_grad:
                    m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                if m.bias.requires_grad:
                    m.bias.data.zero_()
                    
class Regressor(nn.Module):
    def __init__(self, _num_stages=3, _use_avg_on_conv3=True, indim=384, num_classes=8):
        super(Regressor, self).__init__()
        self.nin = NetworkInNetwork(_num_stages=_num_stages, _use_avg_on_conv3=_use_avg_on_conv3)
        self.fc = nn.Linear(indim, num_classes)
        self.fc2 = nn.Linear(indim, num_classes)
        for m in self.modules():
            if isinstance(m, nn.Linear):
                m.bias.data.zero_()
    
    def forward(self, x1, x2, im_type1, im_type2, out_feat_keys=None):
        x1 = self.nin(x1, im_type1, out_feat_keys)
        x2 = self.nin(x2, im_type2, out_feat_keys)
        if out_feat_keys==None:
            x = torch.cat((x1,x2), dim=1)
            return x1, x2, self.fc(x), self.fc2(x)
        else:
            return x1, x2


class Avd_NIN(nn.Module):
    def __init__(self, num_classes):
        super(Avd_NIN, self).__init__()
        self.num_classes = num_classes
        self.features = SequentialADV(
            nn.Conv2d(3, 192, 5, padding=2),
            ADVBN(192),
            nn.ReLU(inplace=True),
            nn.Conv2d(192, 160, 1),
            ADVBN(160),
            nn.ReLU(inplace=True),
            nn.Conv2d(160, 96, 1),
            ADVBN(96),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(3, stride=2, ceil_mode=True),
            nn.Dropout(inplace=True),
            nn.Conv2d(96, 192, 5, padding=2),
            ADVBN(192),
            nn.ReLU(inplace=True),
            nn.Conv2d(192, 192, 1),
            ADVBN(192),
            nn.ReLU(inplace=True),
            nn.Conv2d(192, 192, 1),
            ADVBN(192),
            nn.ReLU(inplace=True),
            nn.AvgPool2d(3, stride=2, ceil_mode=True),
            nn.Dropout(inplace=True),
            nn.Conv2d(192, 192, 3, padding=1),
            ADVBN(192),
            nn.ReLU(inplace=True),
            nn.Conv2d(192, 192, 1),
            ADVBN(192),
            nn.ReLU(inplace=True),
            nn.Conv2d(192, self.num_classes, 1),
            nn.ReLU(inplace=True),
            nn.AvgPool2d(8, stride=1)
        )
        self._initialize_weights()
    def forward(self, x, im_type):
        x = self.features(x, im_type)
        x = x.view(x.size(0), self.num_classes)
        return x
    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                m.weight.data.normal_(0, 0.05)
                if m.bias is not None:
                    m.bias.data.zero_()
class ADVBN(nn.Module):
    def __init__(self, in_features):
        super(ADVBN, self).__init__()
        self.nat = nn.BatchNorm2d(in_features)
        self.adv = nn.BatchNorm2d(in_features)
    def forward(self, x, im_type):
        if im_type == 'nat':
            return self.nat(x)
        return self.adv(x)
class SequentialADV(nn.Sequential):
    def __init__(self, *args):
        super(SequentialADV, self).__init__(*args)
    def forward(self, input, im_type):
        for module in self:
            if isinstance(module, ADVBN):
                input = module(input, im_type)
            else:
                input = module(input)
        return input
