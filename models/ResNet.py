# -*- coding: utf-8 -*-

import math
import copy
import torch as t
from .BasicModule import BasicModule
from torch import nn
from torch.nn import functional as F
from torchvision import models
from torch.autograd import Variable


class ResidualBlock(nn.Module):
    """
    实现子module: Residual Block
    """
    def __init__(self, inchannel, outchannel, stride=1, shortcut=None):
        super(ResidualBlock, self).__init__()
        self.left = nn.Sequential(
            nn.Conv2d(inchannel, outchannel, 3, stride, 1, bias=False),
            nn.BatchNorm2d(outchannel),
            nn.ReLU(inplace=True),
            nn.Conv2d(outchannel, outchannel, 3, 1, 1, bias=False),
            nn.BatchNorm2d(outchannel)
        )
        self.right = shortcut

    def forward(self, x):
        out = self.left(x)
        residual = x if self.right is None else self.right(x)
        out += residual
        return F.relu(out)


class ResNet34(BasicModule):
    """
    实现主module：ResNet34
    ResNet34包含多个layer，每个layer又包含多个Residual block
    用子module来实现Residual block，用_make_layer函数来实现layer
    """

    def __init__(self, num_classes=2):
        super(ResNet34, self).__init__()
        self.model_name = 'resnet34'

        # 前几层： 图像转换
        self.pre = nn.Sequential(
            nn.Conv2d(3, 64, 7, 2, 3, bias=False),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(3, 2, 1)
        )

        # 重复的layer，分别有 3，4，6，3 个residual block
        self.layer1 = self._make_layer(64, 128, 3)
        self.layer2 = self._make_layer(128, 256, 4, stride=2)
        self.layer3 = self._make_layer(256, 512, 6, stride=2)
        self.layer4 = self._make_layer(512, 512, 3, stride=2)

        # 分类用的全连接
        self.fc = nn.Linear(512, num_classes)

    def _make_layer(self, inchannel, outchannel, block_num, stride=1):
        """
        构建layer,包含多个residual block
        """
        shortcut = nn.Sequential(
            nn.Conv2d(inchannel, outchannel, 1, stride, bias=False),
            nn.BatchNorm2d(outchannel)
        )

        layers = list()
        layers.append(ResidualBlock(inchannel, outchannel, stride, shortcut))

        for i in range(1, block_num):
            layers.append(ResidualBlock(outchannel, outchannel))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.pre(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = F.avg_pool2d(x, 7)
        x = x.view(x.size(0), -1)
        return self.fc(x)


class ResNet152(BasicModule):

    def __init__(self, num_classes=2):
        model = models.resnet152(pretrained=True)

        super(ResNet152, self).__init__()

        self.conv1 = model.conv1
        self.bn1 = model.bn1
        self.relu = model.relu
        self.maxpool = model.maxpool
        self.layer1 = model.layer1
        self.layer2 = model.layer2
        self.layer3 = model.layer3
        self.layer4 = model.layer4
        self.avgpool = model.avgpool
        self.fc = nn.Linear(2048, num_classes)

        self.ada_pooling = nn.AdaptiveAvgPool2d((1, 1))

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = self.ada_pooling(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)

        return x


class MultiBranchResNet101(BasicModule):

    def __init__(self, num_classes=2):
        model = models.resnet101(pretrained=True)

        super(MultiBranchResNet101, self).__init__()

        # shared layers
        self.conv1 = model.conv1
        self.bn1 = model.bn1
        self.relu = model.relu
        self.maxpool = model.maxpool
        self.layer1 = model.layer1
        self.layer2 = model.layer2
        self.layer3 = model.layer3

        # specific layers
        for x in ['XR_ELBOW', 'XR_FINGER', 'XR_FOREARM', 'XR_HAND', 'XR_HUMERUS', 'XR_SHOULDER', 'XR_WRIST']:
            setattr(self, f'layer4_{x}', copy.deepcopy(model.layer4))
            setattr(self, f'avgpool_{x}', copy.deepcopy(model.avgpool))
            setattr(self, f'ada_pooling_{x}', nn.AdaptiveAvgPool2d((1, 1)))
            setattr(self, f'fc_{x}', nn.Linear(2048, num_classes))

    def forward(self, x, body_part):
        # shared layers
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)

        # specific layers
        out1 = Variable(t.FloatTensor())
        for (xx, bp) in zip(x, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'layer4_{bp}')(d)
            if out1.size():
                out1 = t.cat([out1, d], dim=0)
            else:
                out1 = d

        out2 = Variable(t.FloatTensor())
        for (xx, bp) in zip(out1, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'avgpool_{bp}')(d)
            if out2.size():
                out2 = t.cat([out2, d], dim=0)
            else:
                out2 = d

        out3 = Variable(t.FloatTensor())
        for (xx, bp) in zip(out2, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'ada_pooling_{bp}')(d)
            if out3.size():
                out3 = t.cat([out3, d], dim=0)
            else:
                out3 = d

        out4 = out3.view(out3.size(0), -1)
        
        out5 = Variable(t.FloatTensor())
        for (xx, bp) in zip(out4, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'fc_{bp}')(d)
            if out5.size():
                out5 = t.cat([out5, d], dim=0)
            else:
                out5 = d

        return out5


class MultiBranchResNet50(BasicModule):

    def __init__(self, num_classes=2):
        model = models.resnet50(pretrained=True)

        super(MultiBranchResNet50, self).__init__()

        # shared layers
        self.conv1 = model.conv1
        self.bn1 = model.bn1
        self.relu = model.relu
        self.maxpool = model.maxpool
        self.layer1 = model.layer1
        self.layer2 = model.layer2
        self.layer3 = model.layer3

        # specific layers
        for x in ['XR_ELBOW', 'XR_FINGER', 'XR_FOREARM', 'XR_HAND', 'XR_HUMERUS', 'XR_SHOULDER', 'XR_WRIST']:
            setattr(self, f'layer4_{x}', copy.deepcopy(model.layer4))
            setattr(self, f'avgpool_{x}', copy.deepcopy(model.avgpool))
            setattr(self, f'ada_pooling_{x}', nn.AdaptiveAvgPool2d((1, 1)))
            setattr(self, f'fc_{x}', nn.Linear(2048, num_classes))

    def forward(self, x, body_part):
        # shared layers
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)

        # specific layers
        out1 = Variable(t.FloatTensor())
        for (xx, bp) in zip(x, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'layer4_{bp}')(d)
            if out1.size():
                out1 = t.cat([out1, d], dim=0)
            else:
                out1 = d

        out2 = Variable(t.FloatTensor())
        for (xx, bp) in zip(out1, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'avgpool_{bp}')(d)
            if out2.size():
                out2 = t.cat([out2, d], dim=0)
            else:
                out2 = d

        out3 = Variable(t.FloatTensor())
        for (xx, bp) in zip(out2, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'ada_pooling_{bp}')(d)
            if out3.size():
                out3 = t.cat([out3, d], dim=0)
            else:
                out3 = d

        out4 = out3.view(out3.size(0), -1)
        
        out5 = Variable(t.FloatTensor())
        for (xx, bp) in zip(out4, body_part):
            d = xx.unsqueeze(0).cuda()
            d = getattr(self, f'fc_{bp}')(d)
            if out5.size():
                out5 = t.cat([out5, d], dim=0)
            else:
                out5 = d

        return out5
