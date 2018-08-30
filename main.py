# -*- coding: utf-8 -*-

import os
import time
import torch as t
import numpy as np

from pprint import pprint
from tqdm import tqdm
from torch.autograd import Variable
from torch.utils.data import DataLoader
from torch.nn import functional
from torchnet import meter
from sklearn.metrics import cohen_kappa_score

from config import opt
from utils import Visualizer
from dataset import MURA_Dataset, MURAClass_Dataset
from models import DenseNet169, CustomDenseNet169, MultiDenseNet169, ResNet152, MultiResolutionNet


def train(**kwargs):
    opt.parse(kwargs)
    vis = Visualizer(port=2333, env=opt.env)

    # step 1: data
    train_data = MURAClass_Dataset(opt.data_root, opt.train_image_paths, 'all', train=True, test=False)
    val_data = MURAClass_Dataset(opt.data_root, opt.test_image_paths, 'all', train=False, test=True)
    print('Training images:', train_data.__len__(), 'Validation images:', val_data.__len__())

    train_dataloader = DataLoader(train_data, batch_size=opt.batch_size, shuffle=True, num_workers=opt.num_workers)
    val_dataloader = DataLoader(val_data, batch_size=opt.batch_size, shuffle=False, num_workers=opt.num_workers)

    # step 2: configure model
    # model = CustomDenseNet169(num_classes=2)
    # model = MultiDenseNet169(num_classes=2)
    # model = ResNet152(num_classes=2)
    model = MultiResolutionNet(num_classes=2)

    if opt.load_model_path:
        model.load(opt.load_model_path)
    if opt.use_gpu:
        model.cuda()
    if opt.parallel:
        model = t.nn.DataParallel(model, device_ids=[x for x in range(opt.num_of_gpu)])
    # print(model)

    model.train()

    # step 3: criterion and optimizer
    N = 21935
    P = 14873
    weight = t.Tensor([P/(P+N), N/(P+N)])
    if opt.use_gpu:
        weight = weight.cuda()

    criterion = t.nn.CrossEntropyLoss(weight=weight)
    lr = opt.lr
    if opt.parallel:
        # optimizer = t.optim.Adam(model.module.get_config_optim(opt.lr, opt.lr_pre), lr=lr, weight_decay=opt.weight_decay)
        optimizer = t.optim.Adam(model.parameters(), lr=lr, weight_decay=opt.weight_decay)
    else:
        # optimizer = t.optim.Adam(model.get_config_optim(opt.lr, opt.lr_pre), lr=lr, weight_decay=opt.weight_decay)
        optimizer = t.optim.Adam(model.parameters(), lr=lr, weight_decay=opt.weight_decay)

    # step 4: meters
    softmax = functional.softmax
    loss_meter = meter.AverageValueMeter()
    train_cm = meter.ConfusionMeter(2)
    previous_loss = 100
    previous_acc = 0

    # step 5: train
    if opt.parallel:
        if not os.path.exists(os.path.join('checkpoints', model.module.model_name)):
            os.mkdir(os.path.join('checkpoints', model.module.model_name))
    else:
        if not os.path.exists(os.path.join('checkpoints', model.model_name)):
            os.mkdir(os.path.join('checkpoints', model.model_name))

    for epoch in range(opt.max_epoch):
        loss_meter.reset()
        train_cm.reset()

        for i, (image, label, body_part, image_path) in tqdm(enumerate(train_dataloader)):
            # train model
            img = Variable(image)
            target = Variable(label)
            body_part = Variable(body_part)
            if opt.use_gpu:
                img = img.cuda()
                target = target.cuda()
                body_part = body_part.cuda()

            score = model(img)
            # score = model(input, body_part)

            optimizer.zero_grad()
            loss = criterion(score, target)
            loss.backward()
            optimizer.step()

            # meters update and visualize
            loss_meter.add(loss.data[0])
            train_cm.add(softmax(score, dim=1).data, target.data)

            if i % opt.print_freq == opt.print_freq - 1:
                vis.plot('loss', loss_meter.value()[0])
                print('loss', loss_meter.value()[0])

                # debug
                if os.path.exists(opt.debug_file):
                    import ipdb
                    ipdb.set_trace()

        # print results
        train_accuracy = 100. * (train_cm.value()[0][0] + train_cm.value()[1][1]) / train_cm.value().sum()
        val_cm, val_accuracy, val_loss = val(model, val_dataloader)

        if val_accuracy > previous_acc:
            if opt.parallel:
                model.module.save(os.path.join('checkpoints', model.module.model_name, model.module.model_name + '_best_model.pth'))
            else:
                # model.save(os.path.join('checkpoints', model.model_name, model.model_name + '_best_model.pth'))
                model.save(os.path.join('checkpoints', model.model_name, 'MultiResolution_combine1_best_model.pth'))
            print('Best Model Saved!')
            previous_acc = val_accuracy

        vis.plot_many({'train_accuracy': train_accuracy, 'val_accuracy': val_accuracy})
        vis.log("epoch: [{epoch}/{total_epoch}], lr: {lr}, loss: {loss}".format(
            epoch=epoch+1, total_epoch=opt.max_epoch, lr=lr, loss=loss_meter.value()[0]))
        vis.log('train_cm:')
        vis.log(train_cm.value())
        vis.log('val_cm:')
        vis.log(val_cm.value())
        print('train_accuracy:', train_accuracy, 'val_accuracy:', val_accuracy)
        print("epoch: [{epoch}/{total_epoch}], lr:{lr}, loss:{loss}".format(
            epoch=epoch+1, total_epoch=opt.max_epoch, lr=lr, loss=loss_meter.value()[0]))
        print('train_cm:')
        print(train_cm.value())
        print('val_cm:')
        print(val_cm.value())

        # update learning rate
        if loss_meter.value()[0] > previous_loss:
            lr = lr * opt.lr_decay
            # 第二种降低学习率的方法:不会有moment等信息的丢失
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr
        previous_loss = loss_meter.value()[0]


def val(model, dataloader):
    """
    计算模型在验证集上的准确率等信息
    """
    model.eval()
    val_cm = meter.ConfusionMeter(2)
    softmax = functional.softmax

    criterion = t.nn.CrossEntropyLoss()
    loss_meter = meter.AverageValueMeter()

    for i, (image, label, body_part, image_path) in tqdm(enumerate(dataloader)):
        img = Variable(image, volatile=True)
        target = Variable(label)
        body_part = Variable(body_part)
        if opt.use_gpu:
            img = img.cuda()
            target = target.cuda()
            body_part = body_part.cuda()

        # score = model(val_input, body_part)
        score = model(img)

        loss = criterion(score, target)
        loss_meter.add(loss.data[0])
        val_cm.add(softmax(score, dim=1).data, target.data)  # use for separate body part
        # confusion_matrix.add(s(Variable(score.data.squeeze())).data, label.type(t.LongTensor)) # original used

    model.train()
    val_accuracy = 100. * (val_cm.value()[0][0] + val_cm.value()[1][1]) / (val_cm.value().sum())

    return val_cm, val_accuracy, loss_meter.value()[0]


def test(**kwargs):
    opt.parse(kwargs)

    # data
    test_data = MURAClass_Dataset(opt.data_root, opt.test_image_paths, 'all', train=False, test=True)
    test_dataloader = DataLoader(test_data, batch_size=opt.batch_size, shuffle=False, num_workers=opt.num_workers)

    # configure model
    # model = DenseNet169(num_classes=2)
    # model = CustomDenseNet169(num_classes=2)
    # model = MultiDenseNet169(num_classes=2)
    # model = ResNet152(num_classes=2)
    model = MultiResolutionNet(num_classes=2)

    if opt.load_model_path:
        model.load(opt.load_model_path)
        print('Model has been loaded!')
    else:
        print("Don't load model!")
    if opt.use_gpu:
        model.cuda()
    model.eval()

    test_cm = meter.ConfusionMeter(2)
    softmax = functional.softmax
    results = []

    for i, (image, label, body_part, image_path) in tqdm(enumerate(test_dataloader)):
        img = Variable(image, volatile=True)
        target = Variable(label)
        if opt.use_gpu:
            img = img.cuda()
            target = target.cuda()

        # score = model(input, body_part)
        score = model(img)

        test_cm.add(softmax(score, dim=1).data, target.data)

        probability = softmax(score, dim=1)[:, 0].data.tolist()
        # label = score.max(dim = 1)[1].data.tolist()

        # 每一行为 图片路径 和 positive的概率
        batch_results = [(path_, probability_) for path_, probability_ in zip(image_path, probability)]

        results += batch_results

    accuracy = 100. * (test_cm.value()[0][0] + test_cm.value()[1][1]) / (test_cm.value().sum())

    print('confusion matrix: ')
    print(test_cm.value())
    print(f'accuracy: {accuracy}')

    write_csv(results, opt.result_file)

    calculate_cohen_kappa()


def write_csv(results, file_name):
    import csv
    with open(file_name, 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['image', 'probability'])
        writer.writerows(results)


def calculate_cohen_kappa(threshold=0.5):
    input_csv_file_path = 'result.csv'

    result_dict = {}
    with open(input_csv_file_path, 'r') as F:
        d = F.readlines()[1:]
        for data in d:
            (path, prob) = data.split(',')

            folder_path = path[:path.rfind('/')]
            prob = float(prob)

            if folder_path in result_dict.keys():
                result_dict[folder_path].append(prob)
            else:
                result_dict[folder_path] = [prob]

    for k, v in result_dict.items():
        result_dict[k] = np.mean(v)
        # visualize
        # print(k, result_dict[k])

    XR_type_list = ['XR_ELBOW', 'XR_FINGER', 'XR_FOREARM', 'XR_HAND', 'XR_HUMERUS', 'XR_SHOULDER', 'XR_WRIST']

    XR_acc_list = []
    XR_kappa_list = []

    for XR_type in XR_type_list:

        # 提取出 XR_type 下的所有folder路径，即 result_dict 中的key
        keys = [k for k, v in result_dict.items() if k.split('/')[6] == XR_type]

        y_true = [1 if key.split('_')[-1] == 'positive' else 0 for key in keys]
        y_pred = [0 if result_dict[key] >= threshold else 1 for key in keys]

        print('--------------------------------------------')
        # print(XR_type)
        # print(y_true[:20])
        # print(y_pred[:20])

        kappa_score = cohen_kappa_score(y_true, y_pred)

        print(XR_type, kappa_score)

        # 预测准确的个数
        count = 0
        for i in range(len(y_true)):
            if y_pred[i] == y_true[i]:
                count += 1
        print(XR_type, 'Accuracy', 100.0 * count / len(y_true))
        XR_acc_list.append(100.0 * count / len(y_true))
        XR_kappa_list.append(kappa_score)

    print('--------------------------------------------')
    print("Overall Acc:", sum(XR_acc_list) / 7)
    print("Overall kappa:", sum(XR_kappa_list) / 7)


def help(**kwargs):
    """
        打印帮助的信息： python main.py help
        """

    print("""
        usage : python main.py <function> [--args=value]
        <function> := train | test | help
        example: 
                python {0} train --env='env_MURA' --lr=0.001
                python {0} test --dataset='/path/to/dataset/root/'
                python {0} help
        avaiable args:""".format(__file__))

    from inspect import getsource
    source = (getsource(opt.__class__))
    print(source)


if __name__ == '__main__':
    import fire

    fire.Fire()

    # model = ResNet152(num_classes=2)
    #
    # if opt.load_model_path:
    #     model.load(opt.load_model_path)
    # if opt.use_gpu:
    #     model.cuda()
    # model.eval()
    #
    # test_data = MURAClass_Dataset(opt.data_root, opt.test_image_paths, 'all', train=False, test=True)
    # test_dataloader = DataLoader(test_data, batch_size=opt.batch_size, shuffle=False, num_workers=opt.num_workers)
    #
    # a,b,c = val(model, test_dataloader)
    #
    # print(a.value(), b, c)
