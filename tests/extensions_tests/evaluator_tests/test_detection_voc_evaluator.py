import numpy as np
import unittest

import chainer
from chainer.datasets import TupleDataset
from chainer.iterators import SerialIterator
from chainer import testing

from chainercv.extensions import DetectionVOCEvaluator
from chainercv.utils import generate_random_bbox

from chainercv.utils.testing import attr

from chainermn import create_communicator


class _DetectionStubLink(chainer.Link):

    def __init__(self, bboxes, labels, initial_count=0):
        super(_DetectionStubLink, self).__init__()
        self.count = initial_count
        self.bboxes = bboxes
        self.labels = labels

    def predict(self, imgs):
        n_img = len(imgs)
        bboxes = self.bboxes[self.count:self.count + n_img]
        labels = self.labels[self.count:self.count + n_img]
        scores = [np.ones_like(l) for l in labels]

        self.count += n_img

        return bboxes, labels, scores


class TestDetectionVOCEvaluator(unittest.TestCase):

    def setUp(self):
        bboxes = [generate_random_bbox(5, (256, 324), 24, 120)
                  for _ in range(10)]
        labels = np.ones((10, 5))
        self.dataset = TupleDataset(
            np.random.uniform(size=(10, 3, 32, 48)),
            bboxes,
            labels)
        self.link = _DetectionStubLink(bboxes, labels)
        self.iterator = SerialIterator(
            self.dataset, 5, repeat=False, shuffle=False)
        self.evaluator = DetectionVOCEvaluator(
            self.iterator, self.link, label_names=('cls0', 'cls1', 'cls2'))
        self.expected_ap = 1

    def test_evaluate(self):
        reporter = chainer.Reporter()
        reporter.add_observer('target', self.link)
        with reporter:
            mean = self.evaluator.evaluate()

        # No observation is reported to the current reporter. Instead the
        # evaluator collect results in order to calculate their mean.
        self.assertEqual(len(reporter.observation), 0)

        np.testing.assert_equal(mean['target/map'], self.expected_ap)
        np.testing.assert_equal(mean['target/ap/cls0'], np.nan)
        np.testing.assert_equal(mean['target/ap/cls1'], self.expected_ap)
        np.testing.assert_equal(mean['target/ap/cls2'], np.nan)

    def test_call(self):
        mean = self.evaluator()
        # main is used as default
        np.testing.assert_equal(mean['main/map'], self.expected_ap)
        np.testing.assert_equal(mean['main/ap/cls0'], np.nan)
        np.testing.assert_equal(mean['main/ap/cls1'], self.expected_ap)
        np.testing.assert_equal(mean['main/ap/cls2'], np.nan)

    def test_evaluator_name(self):
        self.evaluator.name = 'eval'
        mean = self.evaluator()
        # name is used as a prefix
        np.testing.assert_equal(mean['eval/main/map'], self.expected_ap)
        np.testing.assert_equal(mean['eval/main/ap/cls0'], np.nan)
        np.testing.assert_equal(mean['eval/main/ap/cls1'], self.expected_ap)
        np.testing.assert_equal(mean['eval/main/ap/cls2'], np.nan)

    def test_current_report(self):
        reporter = chainer.Reporter()
        with reporter:
            mean = self.evaluator()
        # The result is reported to the current reporter.
        self.assertEqual(reporter.observation, mean)


class TestDetectionVOCEvaluatorMPI(unittest.TestCase):

    def setUp(self):
        self.comm = create_communicator('naive')

        batchsize_per_process = 5
        batchsize = batchsize_per_process * self.comm.size
        if self.comm.rank == 0:
            bboxes = [generate_random_bbox(5, (256, 324), 24, 120)
                      for _ in range(10)]
            labels = [np.random.choice(np.arange(3, dtype=np.int32), size=(5,))
                      for _ in range(10)]
        else:
            bboxes = None
            labels = None
        initial_count = self.comm.rank * batchsize_per_process

        bboxes = self.comm.bcast_obj(bboxes)
        labels = self.comm.bcast_obj(labels)
        self.bboxes = bboxes
        self.labels = labels

        self.dataset = TupleDataset(
            np.random.uniform(size=(10, 3, 32, 48)),
            bboxes, labels)
        self.initial_count = initial_count
        self.batchsize = batchsize

    @attr.mpi
    def test_consistency(self):
        reporter = chainer.Reporter()

        if self.comm.rank == 0:
            multi_iterator = SerialIterator(
                self.dataset, self.batchsize, repeat=False, shuffle=False)
        else:
            multi_iterator = None
        multi_link = _DetectionStubLink(
            self.bboxes, self.labels, self.initial_count)
        multi_evaluator = DetectionVOCEvaluator(
            multi_iterator, multi_link,
            label_names=('cls0', 'cls1', 'cls2'),
            comm=self.comm)
        reporter.add_observer('target', multi_link)
        with reporter:
            multi_mean = multi_evaluator.evaluate()

        if self.comm.rank != 0:
            self.assertEqual(multi_mean, {})
            return

        single_iterator = SerialIterator(
            self.dataset, self.batchsize, repeat=False, shuffle=False)
        single_link = _DetectionStubLink(
            self.bboxes, self.labels)
        single_evaluator = DetectionVOCEvaluator(
            single_iterator, single_link,
            label_names=('cls0', 'cls1', 'cls2'))
        reporter.add_observer('target', single_link)
        with reporter:
            single_mean = single_evaluator.evaluate()

        self.assertEqual(set(multi_mean.keys()), set(single_mean.keys()))
        for key in multi_mean.keys():
            np.testing.assert_equal(single_mean[key], multi_mean[key])


testing.run_module(__name__, __file__)
