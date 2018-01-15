"""
Note [Randomized statistical tests]
-----------------------------------

This note describes how to maintain tests in this file as random sources
change. This file contains two types of randomized tests:

1. The easier type of randomized test are tests that should always pass but are
   initialized with random data. If these fail something is wrong, but it's
   fine to use a fixed seed by inheriting from common.TestCase.

2. The trickier tests are statistical tests. These tests explicitly call
   set_rng_seed(n) and are marked "see Note [Randomized statistical tests]".
   These statistical tests have a known positive failure rate
   (we set failure_rate=1e-3 by default). We need to balance strength of these
   tests with annoyance of false alarms. One way that works is to specifically
   set seeds in each of the randomized tests. When a random generator
   occasionally changes (as in #4312 vectorizing the Box-Muller sampler), some
   of these statistical tests may (rarely) fail. If one fails in this case,
   it's fine to increment the seed of the failing test (but you shouldn't need
   to increment it more than once; otherwise something is probably actually
   wrong).
"""

import math
import unittest
from collections import namedtuple
from itertools import product

import torch
from common import TestCase, run_tests, set_rng_seed
from torch.autograd import Variable, grad, gradcheck
from torch.distributions import (Bernoulli, Beta, Categorical, Cauchy, Chi2,
                                 Dirichlet, Exponential, Gamma, Gumbel, Laplace,
                                 Normal, OneHotCategorical, Multinomial, Pareto,
                                 StudentT, Uniform, kl_divergence)
from torch.distributions.dirichlet import _Dirichlet_backward
from torch.distributions.constraints import Constraint, is_dependent
from torch.distributions.utils import _finfo

TEST_NUMPY = True
try:
    import numpy as np
    import scipy.stats
    import scipy.special
except ImportError:
    TEST_NUMPY = False


# Register all distributions for generic tests.
Example = namedtuple('Example', ['Dist', 'params'])
EXAMPLES = [
    Example(Bernoulli, [
        {'probs': Variable(torch.Tensor([0.7, 0.2, 0.4]), requires_grad=True)},
        {'probs': Variable(torch.Tensor([0.3]), requires_grad=True)},
        {'probs': 0.3},
    ]),
    Example(Beta, [
        {
            'alpha': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True),
            'beta': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True),
        },
        {
            'alpha': Variable(torch.exp(torch.randn(4)), requires_grad=True),
            'beta': Variable(torch.exp(torch.randn(4)), requires_grad=True),
        },
    ]),
    Example(Categorical, [
        {'probs': Variable(torch.Tensor([[0.1, 0.2, 0.3], [0.5, 0.3, 0.2]]), requires_grad=True)},
        {'probs': Variable(torch.Tensor([[1.0, 0.0], [0.0, 1.0]]), requires_grad=True)},
    ]),
    Example(Multinomial, [
        {'probs': Variable(torch.Tensor([[0.1, 0.2, 0.3], [0.5, 0.3, 0.2]]), requires_grad=True), 'total_count': 10},
        {'probs': Variable(torch.Tensor([[1.0, 0.0], [0.0, 1.0]]), requires_grad=True), 'total_count': 10},
    ]),
    Example(Cauchy, [
        {'loc': 0.0, 'scale': 1.0},
        {'loc': Variable(torch.Tensor([0.0])), 'scale': 1.0},
        {'loc': Variable(torch.Tensor([[0.0], [0.0]])),
         'scale': Variable(torch.Tensor([[1.0], [1.0]]))}
    ]),
    Example(Chi2, [
        {'df': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)},
        {'df': Variable(torch.exp(torch.randn(1)), requires_grad=True)},
    ]),
    Example(StudentT, [
        {'df': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)},
        {'df': Variable(torch.exp(torch.randn(1)), requires_grad=True)},
    ]),
    Example(Dirichlet, [
        {'alpha': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)},
        {'alpha': Variable(torch.exp(torch.randn(4)), requires_grad=True)},
    ]),
    Example(Exponential, [
        {'rate': Variable(torch.randn(5, 5).abs(), requires_grad=True)},
        {'rate': Variable(torch.randn(1).abs(), requires_grad=True)},
    ]),
    Example(Gamma, [
        {
            'alpha': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True),
            'beta': Variable(torch.exp(torch.randn(2, 3)), requires_grad=True),
        },
        {
            'alpha': Variable(torch.exp(torch.randn(1)), requires_grad=True),
            'beta': Variable(torch.exp(torch.randn(1)), requires_grad=True),
        },
    ]),
    Example(Gumbel, [
        {
            'loc': Variable(torch.randn(5, 5), requires_grad=True),
            'scale': Variable(torch.randn(5, 5).abs(), requires_grad=True),
        },
        {
            'loc': Variable(torch.randn(1), requires_grad=True),
            'scale': Variable(torch.randn(1).abs(), requires_grad=True),
        },
    ]),
    Example(Laplace, [
        {
            'loc': Variable(torch.randn(5, 5), requires_grad=True),
            'scale': Variable(torch.randn(5, 5).abs(), requires_grad=True),
        },
        {
            'loc': Variable(torch.randn(1), requires_grad=True),
            'scale': Variable(torch.randn(1).abs(), requires_grad=True),
        },
        {
            'loc': torch.Tensor([1.0, 0.0]),
            'scale': torch.Tensor([1e-5, 1e-5]),
        },
    ]),
    Example(Normal, [
        {
            'mean': Variable(torch.randn(5, 5), requires_grad=True),
            'std': Variable(torch.randn(5, 5).abs(), requires_grad=True),
        },
        {
            'mean': Variable(torch.randn(1), requires_grad=True),
            'std': Variable(torch.randn(1).abs(), requires_grad=True),
        },
        {
            'mean': torch.Tensor([1.0, 0.0]),
            'std': torch.Tensor([1e-5, 1e-5]),
        },
    ]),
    Example(OneHotCategorical, [
        {'probs': Variable(torch.Tensor([[0.1, 0.2, 0.3], [0.5, 0.3, 0.2]]), requires_grad=True)},
        {'probs': Variable(torch.Tensor([[1.0, 0.0], [0.0, 1.0]]), requires_grad=True)},
    ]),
    Example(Pareto, [
        {
            'scale': 1.0,
            'alpha': 1.0
        },
        {
            'scale': Variable(torch.randn(5, 5).abs(), requires_grad=True),
            'alpha': Variable(torch.randn(5, 5).abs(), requires_grad=True)
        },
        {
            'scale': torch.Tensor([1.0]),
            'alpha': 1.0
        }
    ]),
    Example(Uniform, [
        {
            'low': Variable(torch.zeros(5, 5), requires_grad=True),
            'high': Variable(torch.ones(5, 5), requires_grad=True),
        },
        {
            'low': Variable(torch.zeros(1), requires_grad=True),
            'high': Variable(torch.ones(1), requires_grad=True),
        },
        {
            'low': torch.Tensor([1.0, 1.0]),
            'high': torch.Tensor([2.0, 3.0]),
        },
    ]),
]


def unwrap(value):
    if isinstance(value, Variable):
        return value.data
    return value


class TestDistributions(TestCase):
    def _gradcheck_log_prob(self, dist_ctor, ctor_params):
        # performs gradient checks on log_prob
        distribution = dist_ctor(*ctor_params)
        s = distribution.sample()

        expected_shape = distribution.batch_shape + distribution.event_shape
        if not expected_shape:
            expected_shape = torch.Size((1,))  # Work around lack of scalars.
        self.assertEqual(s.size(), expected_shape)

        def apply_fn(*params):
            return dist_ctor(*params).log_prob(s)

        gradcheck(apply_fn, ctor_params, raise_exception=True)

    def _check_log_prob(self, dist, asset_fn):
        # checks that the log_prob matches a reference function
        s = dist.sample()
        log_probs = dist.log_prob(s)
        for i, (val, log_prob) in enumerate(zip(s.data.view(-1), log_probs.data.view(-1))):
            asset_fn(i, val, log_prob)

    def _check_sampler_sampler(self, torch_dist, ref_dist, message, multivariate=False,
                               num_samples=10000, failure_rate=1e-3):
        # Checks that the .sample() method matches a reference function.
        torch_samples = torch_dist.sample_n(num_samples).squeeze()
        if isinstance(torch_samples, Variable):
            torch_samples = torch_samples.data
        torch_samples = torch_samples.cpu().numpy()
        ref_samples = ref_dist.rvs(num_samples)
        if multivariate:
            # Project onto a random axis.
            axis = np.random.normal(size=torch_samples.shape[-1])
            axis /= np.linalg.norm(axis)
            torch_samples = np.dot(torch_samples, axis)
            ref_samples = np.dot(ref_samples, axis)
        samples = [(x, +1) for x in torch_samples] + [(x, -1) for x in ref_samples]
        samples.sort()
        samples = np.array(samples)[:, 1]

        # Aggragate into bins filled with roughly zero-mean unit-variance RVs.
        num_bins = 10
        samples_per_bin = len(samples) // num_bins
        bins = samples.reshape((num_bins, samples_per_bin)).mean(axis=1)
        stddev = samples_per_bin ** -0.5
        threshold = stddev * scipy.special.erfinv(1 - 2 * failure_rate / num_bins)
        message = '{}.sample() is biased:\n{}'.format(message, bins)
        for bias in bins:
            self.assertLess(-threshold, bias, message)
            self.assertLess(bias, threshold, message)

    def _check_enumerate_support(self, dist, examples):
        for param, expected in examples:
            param = torch.Tensor(param)
            expected = torch.Tensor(expected)
            actual = dist(param).enumerate_support()
            self.assertEqual(actual, expected)
            param = Variable(param)
            expected = Variable(expected)
            actual = dist(param).enumerate_support()
            self.assertEqual(actual, expected)

    def test_enumerate_support_type(self):
        for Dist, params in EXAMPLES:
            for i, param in enumerate(params):
                dist = Dist(**param)
                try:
                    self.assertTrue(type(unwrap(dist.sample())) is type(unwrap(dist.enumerate_support())),
                                    msg=('{} example {}/{}, return type mismatch between ' +
                                         'sample and enumerate_support.').format(Dist.__name__, i, len(params)))
                except NotImplementedError:
                    pass

    def test_bernoulli(self):
        p = Variable(torch.Tensor([0.7, 0.2, 0.4]), requires_grad=True)
        r = Variable(torch.Tensor([0.3]), requires_grad=True)
        s = 0.3
        self.assertEqual(Bernoulli(p).sample_n(8).size(), (8, 3))
        self.assertTrue(isinstance(Bernoulli(p).sample().data, torch.Tensor))
        self.assertEqual(Bernoulli(r).sample_n(8).size(), (8, 1))
        self.assertEqual(Bernoulli(r).sample().size(), (1,))
        self.assertEqual(Bernoulli(r).sample((3, 2)).size(), (3, 2, 1))
        self.assertEqual(Bernoulli(s).sample().size(), (1,))
        self._gradcheck_log_prob(Bernoulli, (p,))

        def ref_log_prob(idx, val, log_prob):
            prob = p.data[idx]
            self.assertEqual(log_prob, math.log(prob if val else 1 - prob))

        self._check_log_prob(Bernoulli(p), ref_log_prob)
        self._check_log_prob(Bernoulli(logits=p.log() - (-p).log1p()), ref_log_prob)
        self.assertRaises(NotImplementedError, Bernoulli(r).rsample)

        # check entropy computation
        self.assertEqual(Bernoulli(p).entropy().data, torch.Tensor([0.6108, 0.5004, 0.6730]), prec=1e-4)
        self.assertEqual(Bernoulli(torch.Tensor([0.0])).entropy(), torch.Tensor([0.0]))
        self.assertEqual(Bernoulli(s).entropy(), torch.Tensor([0.6108]), prec=1e-4)

    def test_bernoulli_enumerate_support(self):
        examples = [
            ([0.1], [[0], [1]]),
            ([0.1, 0.9], [[0, 0], [1, 1]]),
            ([[0.1, 0.2], [0.3, 0.4]], [[[0, 0], [0, 0]], [[1, 1], [1, 1]]]),
        ]
        self._check_enumerate_support(Bernoulli, examples)

    def test_bernoulli_3d(self):
        p = Variable(torch.Tensor(2, 3, 5).fill_(0.5), requires_grad=True)
        self.assertEqual(Bernoulli(p).sample().size(), (2, 3, 5))
        self.assertEqual(Bernoulli(p).sample(sample_shape=(2, 5)).size(),
                         (2, 5, 2, 3, 5))
        self.assertEqual(Bernoulli(p).sample_n(2).size(), (2, 2, 3, 5))

    def test_multinomial_1d(self):
        total_count = 10
        p = Variable(torch.Tensor([0.1, 0.2, 0.3]), requires_grad=True)
        self.assertEqual(Multinomial(total_count, p).sample().size(), (3,))
        self.assertEqual(Multinomial(total_count, p).sample((2, 2)).size(), (2, 2, 3))
        self.assertEqual(Multinomial(total_count, p).sample_n(1).size(), (1, 3))
        self._gradcheck_log_prob(lambda p: Multinomial(total_count, p), [p])
        self._gradcheck_log_prob(lambda p: Multinomial(total_count, None, p.log()), [p])
        self.assertRaises(NotImplementedError, Multinomial(10, p).rsample)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_multinomial_1d_log_prob(self):
        total_count = 10
        p = Variable(torch.Tensor([0.1, 0.2, 0.3]), requires_grad=True)
        dist = Multinomial(total_count, probs=p)
        x = dist.sample()
        log_prob = dist.log_prob(x)
        expected = torch.Tensor(scipy.stats.multinomial.logpmf(x.numpy(), n=total_count, p=dist.probs.detach().numpy()))
        self.assertEqual(log_prob.data, expected)

        dist = Multinomial(total_count, logits=p.log())
        x = dist.sample()
        log_prob = dist.log_prob(x)
        expected = torch.Tensor(scipy.stats.multinomial.logpmf(x.numpy(), n=total_count, p=dist.probs.detach().numpy()))
        self.assertEqual(log_prob.data, expected)

    def test_multinomial_2d(self):
        total_count = 10
        probabilities = [[0.1, 0.2, 0.3], [0.5, 0.3, 0.2]]
        probabilities_1 = [[1.0, 0.0], [0.0, 1.0]]
        p = Variable(torch.Tensor(probabilities), requires_grad=True)
        s = Variable(torch.Tensor(probabilities_1), requires_grad=True)
        self.assertEqual(Multinomial(total_count, p).sample().size(), (2, 3))
        self.assertEqual(Multinomial(total_count, p).sample(sample_shape=(3, 4)).size(), (3, 4, 2, 3))
        self.assertEqual(Multinomial(total_count, p).sample_n(6).size(), (6, 2, 3))
        set_rng_seed(0)
        self._gradcheck_log_prob(lambda p: Multinomial(total_count, p), [p])
        p.grad.zero_()
        self._gradcheck_log_prob(lambda p: Multinomial(total_count, None, p.log()), [p])

        # sample check for extreme value of probs
        self.assertEqual(Multinomial(total_count, s).sample().data,
                         torch.Tensor([[total_count, 0], [0, total_count]]))

        # check entropy computation
        self.assertRaises(NotImplementedError, Multinomial(10, p).entropy)

    def test_categorical_1d(self):
        p = Variable(torch.Tensor([0.1, 0.2, 0.3]), requires_grad=True)
        # TODO: this should return a 0-dim tensor once we have Scalar support
        self.assertEqual(Categorical(p).sample().size(), (1,))
        self.assertTrue(isinstance(Categorical(p).sample().data, torch.LongTensor))
        self.assertEqual(Categorical(p).sample((2, 2)).size(), (2, 2))
        self.assertEqual(Categorical(p).sample_n(1).size(), (1,))
        self._gradcheck_log_prob(Categorical, (p,))
        self.assertRaises(NotImplementedError, Categorical(p).rsample)

    def test_categorical_2d(self):
        probabilities = [[0.1, 0.2, 0.3], [0.5, 0.3, 0.2]]
        probabilities_1 = [[1.0, 0.0], [0.0, 1.0]]
        p = Variable(torch.Tensor(probabilities), requires_grad=True)
        s = Variable(torch.Tensor(probabilities_1), requires_grad=True)
        self.assertEqual(Categorical(p).sample().size(), (2,))
        self.assertEqual(Categorical(p).sample(sample_shape=(3, 4)).size(), (3, 4, 2))
        self.assertEqual(Categorical(p).sample_n(6).size(), (6, 2))
        self._gradcheck_log_prob(Categorical, (p,))

        # sample check for extreme value of probs
        set_rng_seed(0)
        self.assertEqual(Categorical(s).sample(sample_shape=(2,)).data,
                         torch.Tensor([[0, 1], [0, 1]]))

        def ref_log_prob(idx, val, log_prob):
            sample_prob = p.data[idx][val] / p.data[idx].sum()
            self.assertEqual(log_prob, math.log(sample_prob))

        self._check_log_prob(Categorical(p), ref_log_prob)
        self._check_log_prob(Categorical(logits=p.log()), ref_log_prob)

        # check entropy computation
        self.assertEqual(Categorical(p).entropy().data, torch.Tensor([1.0114, 1.0297]), prec=1e-4)
        self.assertEqual(Categorical(s).entropy().data, torch.Tensor([0.0, 0.0]))

    def test_categorical_enumerate_support(self):
        examples = [
            ([0.1, 0.2, 0.7], [0, 1, 2]),
            ([[0.1, 0.9], [0.3, 0.7]], [[0, 0], [1, 1]]),
        ]
        self._check_enumerate_support(Categorical, examples)

    def test_one_hot_categorical_1d(self):
        p = Variable(torch.Tensor([0.1, 0.2, 0.3]), requires_grad=True)
        self.assertEqual(OneHotCategorical(p).sample().size(), (3,))
        self.assertTrue(isinstance(OneHotCategorical(p).sample().data, torch.Tensor))
        self.assertEqual(OneHotCategorical(p).sample((2, 2)).size(), (2, 2, 3))
        self.assertEqual(OneHotCategorical(p).sample_n(1).size(), (1, 3))
        self._gradcheck_log_prob(OneHotCategorical, (p,))
        self.assertRaises(NotImplementedError, OneHotCategorical(p).rsample)

    def test_one_hot_categorical_2d(self):
        probabilities = [[0.1, 0.2, 0.3], [0.5, 0.3, 0.2]]
        probabilities_1 = [[1.0, 0.0], [0.0, 1.0]]
        p = Variable(torch.Tensor(probabilities), requires_grad=True)
        s = Variable(torch.Tensor(probabilities_1), requires_grad=True)
        self.assertEqual(OneHotCategorical(p).sample().size(), (2, 3))
        self.assertEqual(OneHotCategorical(p).sample(sample_shape=(3, 4)).size(), (3, 4, 2, 3))
        self.assertEqual(OneHotCategorical(p).sample_n(6).size(), (6, 2, 3))
        self._gradcheck_log_prob(OneHotCategorical, (p,))

        dist = OneHotCategorical(p)
        x = dist.sample()
        self.assertEqual(dist.log_prob(x), Categorical(p).log_prob(x.max(-1)[1]))

    def test_one_hot_categorical_enumerate_support(self):
        examples = [
            ([0.1, 0.2, 0.7], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
            ([[0.1, 0.9], [0.3, 0.7]], [[[1, 0], [1, 0]], [[0, 1], [0, 1]]]),
        ]
        self._check_enumerate_support(OneHotCategorical, examples)

    def test_uniform(self):
        low = Variable(torch.zeros(5, 5), requires_grad=True)
        high = Variable(torch.ones(5, 5) * 3, requires_grad=True)
        low_1d = Variable(torch.zeros(1), requires_grad=True)
        high_1d = Variable(torch.ones(1) * 3, requires_grad=True)
        self.assertEqual(Uniform(low, high).sample().size(), (5, 5))
        self.assertEqual(Uniform(low, high).sample_n(7).size(), (7, 5, 5))
        self.assertEqual(Uniform(low_1d, high_1d).sample().size(), (1,))
        self.assertEqual(Uniform(low_1d, high_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Uniform(0.0, 1.0).sample_n(1).size(), (1,))

        # Check log_prob computation when value outside range
        uniform = Uniform(low_1d, high_1d)
        above_high = Variable(torch.Tensor([4.0]))
        below_low = Variable(torch.Tensor([-1.0]))
        self.assertEqual(uniform.log_prob(above_high).data[0], -float('inf'), allow_inf=True)
        self.assertEqual(uniform.log_prob(below_low).data[0], -float('inf'), allow_inf=True)

        set_rng_seed(1)
        self._gradcheck_log_prob(Uniform, (low, high))
        self._gradcheck_log_prob(Uniform, (low, 1.0))
        self._gradcheck_log_prob(Uniform, (0.0, high))

        state = torch.get_rng_state()
        rand = low.new(low.size()).uniform_()
        torch.set_rng_state(state)
        u = Uniform(low, high).rsample()
        u.backward(torch.ones_like(u))
        self.assertEqual(low.grad, 1 - rand)
        self.assertEqual(high.grad, rand)
        low.grad.zero_()
        high.grad.zero_()

    def test_cauchy(self):
        loc = Variable(torch.zeros(5, 5), requires_grad=True)
        scale = Variable(torch.ones(5, 5), requires_grad=True)
        loc_1d = Variable(torch.zeros(1), requires_grad=True)
        scale_1d = Variable(torch.ones(1), requires_grad=True)
        self.assertEqual(Cauchy(loc, scale).sample().size(), (5, 5))
        self.assertEqual(Cauchy(loc, scale).sample_n(7).size(), (7, 5, 5))
        self.assertEqual(Cauchy(loc_1d, scale_1d).sample().size(), (1,))
        self.assertEqual(Cauchy(loc_1d, scale_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Cauchy(0.0, 1.0).sample_n(1).size(), (1,))

        set_rng_seed(1)
        self._gradcheck_log_prob(Uniform, (loc, scale))
        self._gradcheck_log_prob(Uniform, (loc, 1.0))
        self._gradcheck_log_prob(Uniform, (0.0, scale))

        state = torch.get_rng_state()
        eps = loc.new(loc.size()).cauchy_()
        torch.set_rng_state(state)
        c = Cauchy(loc, scale).rsample()
        c.backward(torch.ones_like(c))
        self.assertEqual(loc.grad, torch.ones_like(scale))
        self.assertEqual(scale.grad, eps)
        loc.grad.zero_()
        scale.grad.zero_()

    def test_normal(self):
        mean = Variable(torch.randn(5, 5), requires_grad=True)
        std = Variable(torch.randn(5, 5).abs(), requires_grad=True)
        mean_1d = Variable(torch.randn(1), requires_grad=True)
        std_1d = Variable(torch.randn(1), requires_grad=True)
        mean_delta = torch.Tensor([1.0, 0.0])
        std_delta = torch.Tensor([1e-5, 1e-5])
        self.assertEqual(Normal(mean, std).sample().size(), (5, 5))
        self.assertEqual(Normal(mean, std).sample_n(7).size(), (7, 5, 5))
        self.assertEqual(Normal(mean_1d, std_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Normal(mean_1d, std_1d).sample().size(), (1,))
        self.assertEqual(Normal(0.2, .6).sample_n(1).size(), (1,))
        self.assertEqual(Normal(-0.7, 50.0).sample_n(1).size(), (1,))

        # sample check for extreme value of mean, std
        set_rng_seed(1)
        self.assertEqual(Normal(mean_delta, std_delta).sample(sample_shape=(1, 2)),
                         torch.Tensor([[[1.0, 0.0], [1.0, 0.0]]]),
                         prec=1e-4)

        self._gradcheck_log_prob(Normal, (mean, std))
        self._gradcheck_log_prob(Normal, (mean, 1.0))
        self._gradcheck_log_prob(Normal, (0.0, std))

        state = torch.get_rng_state()
        eps = torch.normal(torch.zeros_like(mean), torch.ones_like(std))
        torch.set_rng_state(state)
        z = Normal(mean, std).rsample()
        z.backward(torch.ones_like(z))
        self.assertEqual(mean.grad, torch.ones_like(mean))
        self.assertEqual(std.grad, eps)
        mean.grad.zero_()
        std.grad.zero_()
        self.assertEqual(z.size(), (5, 5))

        def ref_log_prob(idx, x, log_prob):
            m = mean.data.view(-1)[idx]
            s = std.data.view(-1)[idx]
            expected = (math.exp(-(x - m) ** 2 / (2 * s ** 2)) /
                        math.sqrt(2 * math.pi * s ** 2))
            self.assertAlmostEqual(log_prob, math.log(expected), places=3)

        self._check_log_prob(Normal(mean, std), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_normal_sample(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        for mean, std in product([-1.0, 0.0, 1.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(Normal(mean, std),
                                        scipy.stats.norm(loc=mean, scale=std),
                                        'Normal(mean={}, std={})'.format(mean, std))

    def test_exponential(self):
        rate = Variable(torch.randn(5, 5).abs(), requires_grad=True)
        rate_1d = Variable(torch.randn(1).abs(), requires_grad=True)
        self.assertEqual(Exponential(rate).sample().size(), (5, 5))
        self.assertEqual(Exponential(rate).sample((7,)).size(), (7, 5, 5))
        self.assertEqual(Exponential(rate_1d).sample((1,)).size(), (1, 1))
        self.assertEqual(Exponential(rate_1d).sample().size(), (1,))
        self.assertEqual(Exponential(0.2).sample((1,)).size(), (1,))
        self.assertEqual(Exponential(50.0).sample((1,)).size(), (1,))

        self._gradcheck_log_prob(Exponential, (rate,))
        state = torch.get_rng_state()
        eps = rate.new(rate.size()).exponential_()
        torch.set_rng_state(state)
        z = Exponential(rate).rsample()
        z.backward(torch.ones_like(z))
        self.assertEqual(rate.grad, -eps / rate**2)
        rate.grad.zero_()
        self.assertEqual(z.size(), (5, 5))

        def ref_log_prob(idx, x, log_prob):
            m = rate.data.view(-1)[idx]
            expected = math.log(m) - m * x
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(Exponential(rate), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_exponential_sample(self):
        set_rng_seed(1)  # see Note [Randomized statistical tests]
        for rate in [1e-5, 1.0, 10.]:
            self._check_sampler_sampler(Exponential(rate),
                                        scipy.stats.expon(scale=1. / rate),
                                        'Exponential(rate={})'.format(rate))

    def test_laplace(self):
        loc = Variable(torch.randn(5, 5), requires_grad=True)
        scale = Variable(torch.randn(5, 5).abs(), requires_grad=True)
        loc_1d = Variable(torch.randn(1), requires_grad=True)
        scale_1d = Variable(torch.randn(1), requires_grad=True)
        loc_delta = torch.Tensor([1.0, 0.0])
        scale_delta = torch.Tensor([1e-5, 1e-5])
        self.assertEqual(Laplace(loc, scale).sample().size(), (5, 5))
        self.assertEqual(Laplace(loc, scale).sample_n(7).size(), (7, 5, 5))
        self.assertEqual(Laplace(loc_1d, scale_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Laplace(loc_1d, scale_1d).sample().size(), (1,))
        self.assertEqual(Laplace(0.2, .6).sample_n(1).size(), (1,))
        self.assertEqual(Laplace(-0.7, 50.0).sample_n(1).size(), (1,))

        # sample check for extreme value of mean, std
        set_rng_seed(0)
        self.assertEqual(Laplace(loc_delta, scale_delta).sample(sample_shape=(1, 2)),
                         torch.Tensor([[[1.0, 0.0], [1.0, 0.0]]]),
                         prec=1e-4)

        self._gradcheck_log_prob(Laplace, (loc, scale))
        self._gradcheck_log_prob(Laplace, (loc, 1.0))
        self._gradcheck_log_prob(Laplace, (0.0, scale))

        state = torch.get_rng_state()
        eps = torch.ones_like(loc).uniform_(-.5, .5)
        torch.set_rng_state(state)
        z = Laplace(loc, scale).rsample()
        z.backward(torch.ones_like(z))
        self.assertEqual(loc.grad, torch.ones_like(loc))
        self.assertEqual(scale.grad, -eps.sign() * torch.log1p(-2 * eps.abs()))
        loc.grad.zero_()
        scale.grad.zero_()
        self.assertEqual(z.size(), (5, 5))

        def ref_log_prob(idx, x, log_prob):
            m = loc.data.view(-1)[idx]
            s = scale.data.view(-1)[idx]
            expected = (-math.log(2 * s) - abs(x - m) / s)
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(Laplace(loc, scale), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_laplace_sample(self):
        set_rng_seed(1)  # see Note [Randomized statistical tests]
        for loc, scale in product([-1.0, 0.0, 1.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(Laplace(loc, scale),
                                        scipy.stats.laplace(loc=loc, scale=scale),
                                        'Laplace(loc={}, scale={})'.format(loc, scale))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_gamma_shape(self):
        alpha = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        beta = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        alpha_1d = Variable(torch.exp(torch.randn(1)), requires_grad=True)
        beta_1d = Variable(torch.exp(torch.randn(1)), requires_grad=True)
        self.assertEqual(Gamma(alpha, beta).sample().size(), (2, 3))
        self.assertEqual(Gamma(alpha, beta).sample_n(5).size(), (5, 2, 3))
        self.assertEqual(Gamma(alpha_1d, beta_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Gamma(alpha_1d, beta_1d).sample().size(), (1,))
        self.assertEqual(Gamma(0.5, 0.5).sample().size(), (1,))
        self.assertEqual(Gamma(0.5, 0.5).sample_n(1).size(), (1,))

        def ref_log_prob(idx, x, log_prob):
            a = alpha.data.view(-1)[idx]
            b = beta.data.view(-1)[idx]
            expected = scipy.stats.gamma.logpdf(x, a, scale=1 / b)
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(Gamma(alpha, beta), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_gamma_sample(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        for alpha, beta in product([0.1, 1.0, 5.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(Gamma(alpha, beta),
                                        scipy.stats.gamma(alpha, scale=1.0 / beta),
                                        'Gamma(alpha={}, beta={})'.format(alpha, beta))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_pareto_shape(self):
        scale = Variable(torch.randn(2, 3).abs(), requires_grad=True)
        alpha = Variable(torch.randn(2, 3).abs(), requires_grad=True)
        scale_1d = torch.randn(1).abs()
        alpha_1d = torch.randn(1).abs()
        self.assertEqual(Pareto(scale, alpha).sample().size(), (2, 3))
        self.assertEqual(Pareto(scale, alpha).sample_n(5).size(), (5, 2, 3))
        self.assertEqual(Pareto(scale_1d, alpha_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Pareto(scale_1d, alpha_1d).sample().size(), (1,))
        self.assertEqual(Pareto(1.0, 1.0).sample().size(), (1,))
        self.assertEqual(Pareto(1.0, 1.0).sample_n(1).size(), (1,))

        def ref_log_prob(idx, x, log_prob):
            s = scale.data.view(-1)[idx]
            a = alpha.data.view(-1)[idx]
            expected = scipy.stats.pareto.logpdf(x, a, scale=s)
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(Pareto(scale, alpha), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_pareto_sample(self):
        set_rng_seed(1)  # see Note [Randomized statistical tests]
        for scale, alpha in product([0.1, 1.0, 5.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(Pareto(scale, alpha),
                                        scipy.stats.pareto(alpha, scale=scale),
                                        'Pareto(scale={}, alpha={})'.format(scale, alpha))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_gumbel_shape(self):
        loc = Variable(torch.randn(2, 3), requires_grad=True)
        scale = Variable(torch.randn(2, 3).abs(), requires_grad=True)
        loc_1d = torch.randn(1)
        scale_1d = torch.randn(1).abs()
        self.assertEqual(Gumbel(loc, scale).sample().size(), (2, 3))
        self.assertEqual(Gumbel(loc, scale).sample_n(5).size(), (5, 2, 3))
        self.assertEqual(Gumbel(loc_1d, scale_1d).sample().size(), (1,))
        self.assertEqual(Gumbel(loc_1d, scale_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Gumbel(1.0, 1.0).sample().size(), (1,))
        self.assertEqual(Gumbel(1.0, 1.0).sample_n(1).size(), (1,))

        def ref_log_prob(idx, x, log_prob):
            l = loc.data.view(-1)[idx]
            s = scale.data.view(-1)[idx]
            expected = scipy.stats.gumbel_r.logpdf(x, loc=l, scale=s)
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(Gumbel(loc, scale), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_gumbel_sample(self):
        set_rng_seed(1)  # see note [Randomized statistical tests]
        for loc, scale in product([-5.0, -1.0, -0.1, 0.1, 1.0, 5.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(Gumbel(loc, scale),
                                        scipy.stats.gumbel_r(loc=loc, scale=scale),
                                        'Gumbel(loc={}, scale={})'.format(loc, scale))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_chi2_shape(self):
        df = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        df_1d = Variable(torch.exp(torch.randn(1)), requires_grad=True)
        self.assertEqual(Chi2(df).sample().size(), (2, 3))
        self.assertEqual(Chi2(df).sample_n(5).size(), (5, 2, 3))
        self.assertEqual(Chi2(df_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(Chi2(df_1d).sample().size(), (1,))
        self.assertEqual(Chi2(0.5).sample().size(), (1,))
        self.assertEqual(Chi2(0.5).sample_n(1).size(), (1,))

        def ref_log_prob(idx, x, log_prob):
            d = df.data.view(-1)[idx]
            expected = scipy.stats.chi2.logpdf(x, d)
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(Chi2(df), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_chi2_sample(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        for df in [0.1, 1.0, 5.0]:
            self._check_sampler_sampler(Chi2(df),
                                        scipy.stats.chi2(df),
                                        'Chi2(df={})'.format(df))

    @unittest.skipIf(not TEST_NUMPY, "Numpy not found")
    def test_studentT_shape(self):
        df = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        df_1d = Variable(torch.exp(torch.randn(1)), requires_grad=True)
        self.assertEqual(StudentT(df).sample().size(), (2, 3))
        self.assertEqual(StudentT(df).sample_n(5).size(), (5, 2, 3))
        self.assertEqual(StudentT(df_1d).sample_n(1).size(), (1, 1))
        self.assertEqual(StudentT(df_1d).sample().size(), (1,))
        self.assertEqual(StudentT(0.5).sample().size(), (1,))
        self.assertEqual(StudentT(0.5).sample_n(1).size(), (1,))

        def ref_log_prob(idx, x, log_prob):
            d = df.data.view(-1)[idx]
            expected = scipy.stats.t.logpdf(x, d)
            self.assertAlmostEqual(log_prob, expected, places=3)

        self._check_log_prob(StudentT(df), ref_log_prob)

    @unittest.skipIf(not TEST_NUMPY, "Numpy not found")
    def test_studentT_sample(self):
        set_rng_seed(11)  # see Note [Randomized statistical tests]
        for df, loc, scale in product([0.1, 1.0, 5.0, 10.0], [-1.0, 0.0, 1.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(StudentT(df=df, loc=loc, scale=scale),
                                        scipy.stats.t(df=df, loc=loc, scale=scale),
                                        'StudentT(df={}, loc={}, scale={})'.format(df, loc, scale))

    @unittest.skipIf(not TEST_NUMPY, "Numpy not found")
    def test_studentT_log_prob(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        num_samples = 10
        for df, loc, scale in product([0.1, 1.0, 5.0, 10.0], [-1.0, 0.0, 1.0], [0.1, 1.0, 10.0]):
            dist = StudentT(df=df, loc=loc, scale=scale)
            x = dist.sample((num_samples,))
            actual_log_prob = dist.log_prob(x)
            for i in range(num_samples):
                expected_log_prob = scipy.stats.t.logpdf(x[i], df=df, loc=loc, scale=scale)
                self.assertAlmostEqual(actual_log_prob[i], expected_log_prob, places=3)

    def test_dirichlet_shape(self):
        alpha = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        alpha_1d = Variable(torch.exp(torch.randn(4)), requires_grad=True)
        self.assertEqual(Dirichlet(alpha).sample().size(), (2, 3))
        self.assertEqual(Dirichlet(alpha).sample((5,)).size(), (5, 2, 3))
        self.assertEqual(Dirichlet(alpha_1d).sample().size(), (4,))
        self.assertEqual(Dirichlet(alpha_1d).sample((1,)).size(), (1, 4))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_dirichlet_log_prob(self):
        num_samples = 10
        alpha = torch.exp(torch.randn(5))
        dist = Dirichlet(alpha)
        x = dist.sample((num_samples,))
        actual_log_prob = dist.log_prob(x)
        for i in range(num_samples):
            expected_log_prob = scipy.stats.dirichlet.logpdf(x[i].numpy(), alpha.numpy())
            self.assertAlmostEqual(actual_log_prob[i], expected_log_prob, places=3)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_dirichlet_sample(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        alpha = torch.exp(torch.randn(3))
        self._check_sampler_sampler(Dirichlet(alpha),
                                    scipy.stats.dirichlet(alpha.numpy()),
                                    'Dirichlet(alpha={})'.format(list(alpha)),
                                    multivariate=True)

    def test_beta_shape(self):
        alpha = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        beta = Variable(torch.exp(torch.randn(2, 3)), requires_grad=True)
        alpha_1d = Variable(torch.exp(torch.randn(4)), requires_grad=True)
        beta_1d = Variable(torch.exp(torch.randn(4)), requires_grad=True)
        self.assertEqual(Beta(alpha, beta).sample().size(), (2, 3))
        self.assertEqual(Beta(alpha, beta).sample((5,)).size(), (5, 2, 3))
        self.assertEqual(Beta(alpha_1d, beta_1d).sample().size(), (4,))
        self.assertEqual(Beta(alpha_1d, beta_1d).sample((1,)).size(), (1, 4))
        self.assertEqual(Beta(0.1, 0.3).sample().size(), (1,))
        self.assertEqual(Beta(0.1, 0.3).sample((5,)).size(), (5,))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_beta_log_prob(self):
        for _ in range(100):
            alpha = np.exp(np.random.normal())
            beta = np.exp(np.random.normal())
            dist = Beta(alpha, beta)
            x = dist.sample()
            actual_log_prob = dist.log_prob(x).sum()
            expected_log_prob = scipy.stats.beta.logpdf(x, alpha, beta)[0]
            self.assertAlmostEqual(actual_log_prob, expected_log_prob, places=3, allow_inf=True)

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_beta_sample(self):
        set_rng_seed(1)  # see Note [Randomized statistical tests]
        for alpha, beta in product([0.1, 1.0, 10.0], [0.1, 1.0, 10.0]):
            self._check_sampler_sampler(Beta(alpha, beta),
                                        scipy.stats.beta(alpha, beta),
                                        'Beta(alpha={}, beta={})'.format(alpha, beta))
        # Check that small alphas do not cause NANs.
        for Tensor in [torch.FloatTensor, torch.DoubleTensor]:
            x = Beta(Tensor([1e-6]), Tensor([1e-6])).sample()[0]
            self.assertTrue(np.isfinite(x) and x > 0, 'Invalid Beta.sample(): {}'.format(x))

    def test_valid_parameter_broadcasting(self):
        # Test correct broadcasting of parameter sizes for distributions that have multiple
        # parameters.
        # example type (distribution instance, expected sample shape)
        valid_examples = [
            (Normal(mean=torch.Tensor([0, 0]), std=1),
             (2,)),
            (Normal(mean=0, std=torch.Tensor([1, 1])),
             (2,)),
            (Normal(mean=torch.Tensor([0, 0]), std=torch.Tensor([1])),
             (2,)),
            (Normal(mean=torch.Tensor([0, 0]), std=torch.Tensor([[1], [1]])),
             (2, 2)),
            (Normal(mean=torch.Tensor([0, 0]), std=torch.Tensor([[1]])),
             (1, 2)),
            (Normal(mean=torch.Tensor([0]), std=torch.Tensor([[1]])),
             (1, 1)),
            (Gamma(alpha=torch.Tensor([1, 1]), beta=1),
             (2,)),
            (Gamma(alpha=1, beta=torch.Tensor([1, 1])),
             (2,)),
            (Gamma(alpha=torch.Tensor([1, 1]), beta=torch.Tensor([[1], [1], [1]])),
             (3, 2)),
            (Gamma(alpha=torch.Tensor([1, 1]), beta=torch.Tensor([[1], [1]])),
             (2, 2)),
            (Gamma(alpha=torch.Tensor([1, 1]), beta=torch.Tensor([[1]])),
             (1, 2)),
            (Gamma(alpha=torch.Tensor([1]), beta=torch.Tensor([[1]])),
             (1, 1)),
            (Gumbel(loc=torch.Tensor([0, 0]), scale=1),
             (2,)),
            (Gumbel(loc=0, scale=torch.Tensor([1, 1])),
             (2,)),
            (Gumbel(loc=torch.Tensor([0, 0]), scale=torch.Tensor([1])),
             (2,)),
            (Gumbel(loc=torch.Tensor([0, 0]), scale=torch.Tensor([[1], [1]])),
             (2, 2)),
            (Gumbel(loc=torch.Tensor([0, 0]), scale=torch.Tensor([[1]])),
             (1, 2)),
            (Gumbel(loc=torch.Tensor([0]), scale=torch.Tensor([[1]])),
             (1, 1)),
            (Laplace(loc=torch.Tensor([0, 0]), scale=1),
             (2,)),
            (Laplace(loc=0, scale=torch.Tensor([1, 1])),
             (2,)),
            (Laplace(loc=torch.Tensor([0, 0]), scale=torch.Tensor([1])),
             (2,)),
            (Laplace(loc=torch.Tensor([0, 0]), scale=torch.Tensor([[1], [1]])),
             (2, 2)),
            (Laplace(loc=torch.Tensor([0, 0]), scale=torch.Tensor([[1]])),
             (1, 2)),
            (Laplace(loc=torch.Tensor([0]), scale=torch.Tensor([[1]])),
             (1, 1)),
            (Pareto(scale=torch.Tensor([1, 1]), alpha=1),
             (2,)),
            (Pareto(scale=1, alpha=torch.Tensor([1, 1])),
             (2,)),
            (Pareto(scale=torch.Tensor([1, 1]), alpha=torch.Tensor([1])),
             (2,)),
            (Pareto(scale=torch.Tensor([1, 1]), alpha=torch.Tensor([[1], [1]])),
             (2, 2)),
            (Pareto(scale=torch.Tensor([1, 1]), alpha=torch.Tensor([[1]])),
             (1, 2)),
            (Pareto(scale=torch.Tensor([1]), alpha=torch.Tensor([[1]])),
             (1, 1)),
            (StudentT(df=torch.Tensor([1, 1]), loc=1),
             (2,)),
            (StudentT(df=1, scale=torch.Tensor([1, 1])),
             (2,)),
            (StudentT(df=torch.Tensor([1, 1]), loc=torch.Tensor([1])),
             (2,)),
            (StudentT(df=torch.Tensor([1, 1]), scale=torch.Tensor([[1], [1]])),
             (2, 2)),
            (StudentT(df=torch.Tensor([1, 1]), loc=torch.Tensor([[1]])),
             (1, 2)),
            (StudentT(df=torch.Tensor([1]), scale=torch.Tensor([[1]])),
             (1, 1)),
        ]

        for dist, expected_size in valid_examples:
            dist_sample_size = dist.sample().size()
            self.assertEqual(dist_sample_size, expected_size,
                             'actual size: {} != expected size: {}'.format(dist_sample_size, expected_size))

    def test_invalid_parameter_broadcasting(self):
        # invalid broadcasting cases; should throw error
        # example type (distribution class, distribution params)
        invalid_examples = [
            (Normal, {
                'mean': torch.Tensor([[0, 0]]),
                'std': torch.Tensor([1, 1, 1, 1])
            }),
            (Normal, {
                'mean': torch.Tensor([[[0, 0, 0], [0, 0, 0]]]),
                'std': torch.Tensor([1, 1])
            }),
            (Gumbel, {
                'loc': torch.Tensor([[0, 0]]),
                'scale': torch.Tensor([1, 1, 1, 1])
            }),
            (Gumbel, {
                'loc': torch.Tensor([[[0, 0, 0], [0, 0, 0]]]),
                'scale': torch.Tensor([1, 1])
            }),
            (Gamma, {
                'alpha': torch.Tensor([0, 0]),
                'beta': torch.Tensor([1, 1, 1])
            }),
            (Laplace, {
                'loc': torch.Tensor([0, 0]),
                'scale': torch.Tensor([1, 1, 1])
            }),
            (Pareto, {
                'scale': torch.Tensor([1, 1]),
                'alpha': torch.Tensor([1, 1, 1])
            }),
            (Pareto, {
                'scale': torch.Tensor([1, 1]),
                'alpha': torch.Tensor([1, 1, 1])
            }),
            (StudentT, {
                'df': torch.Tensor([1, 1]),
                'scale': torch.Tensor([1, 1, 1])
            }),
            (StudentT, {
                'df': torch.Tensor([1, 1]),
                'loc': torch.Tensor([1, 1, 1])
            })
        ]

        for dist, kwargs in invalid_examples:
            self.assertRaises(RuntimeError, dist, **kwargs)


# These tests are only needed for a few distributions that implement custom
# reparameterized gradients. Most .rsample() implementations simply rely on
# the reparameterization trick and do not need to be tested for accuracy.
class TestRsample(TestCase):
    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_gamma(self):
        num_samples = 100
        for alpha in [1e-2, 1e-1, 1e0, 1e1, 1e2, 1e3, 1e4]:
            alphas = Variable(torch.FloatTensor([alpha] * num_samples), requires_grad=True)
            betas = Variable(torch.ones(num_samples).type_as(alphas))
            x = Gamma(alphas, betas).rsample()
            x.sum().backward()
            x, ind = x.data.sort()
            x = x.numpy()
            actual_grad = alphas.grad.data[ind].numpy()
            # Compare with expected gradient dx/dalpha along constant cdf(x,alpha).
            cdf = scipy.stats.gamma.cdf
            pdf = scipy.stats.gamma.pdf
            eps = 0.01 * alpha / (1.0 + alpha ** 0.5)
            cdf_alpha = (cdf(x, alpha + eps) - cdf(x, alpha - eps)) / (2 * eps)
            cdf_x = pdf(x, alpha)
            expected_grad = -cdf_alpha / cdf_x
            rel_error = np.abs(actual_grad - expected_grad) / (expected_grad + 1e-30)
            self.assertLess(np.max(rel_error), 0.0005, '\n'.join([
                'Bad gradient dx/alpha for x ~ Gamma({}, 1)'.format(alpha),
                'x {}'.format(x),
                'expected {}'.format(expected_grad),
                'actual {}'.format(actual_grad),
                'rel error {}'.format(rel_error),
                'max error {}'.format(rel_error.max()),
                'at alpha={}, x={}'.format(alpha, x[rel_error.argmax()]),
            ]))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_chi2(self):
        num_samples = 100
        for df in [1e-2, 1e-1, 1e0, 1e1, 1e2, 1e3, 1e4]:
            dfs = Variable(torch.FloatTensor([df] * num_samples), requires_grad=True)
            x = Chi2(dfs).rsample()
            x.sum().backward()
            x, ind = x.data.sort()
            x = x.numpy()
            actual_grad = dfs.grad.data[ind].numpy()
            # Compare with expected gradient dx/ddf along constant cdf(x,df).
            cdf = scipy.stats.chi2.cdf
            pdf = scipy.stats.chi2.pdf
            eps = 0.01 * df / (1.0 + df ** 0.5)
            cdf_df = (cdf(x, df + eps) - cdf(x, df - eps)) / (2 * eps)
            cdf_x = pdf(x, df)
            expected_grad = -cdf_df / cdf_x
            rel_error = np.abs(actual_grad - expected_grad) / (expected_grad + 1e-30)
            self.assertLess(np.max(rel_error), 0.001, '\n'.join([
                'Bad gradient dx/ddf for x ~ Chi2({})'.format(df),
                'x {}'.format(x),
                'expected {}'.format(expected_grad),
                'actual {}'.format(actual_grad),
                'rel error {}'.format(rel_error),
                'max error {}'.format(rel_error.max()),
            ]))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_dirichlet_on_diagonal(self):
        num_samples = 20
        grid = [1e-1, 1e0, 1e1]
        for a0, a1, a2 in product(grid, grid, grid):
            alphas = Variable(torch.FloatTensor([[a0, a1, a2]] * num_samples), requires_grad=True)
            x = Dirichlet(alphas).rsample()[:, 0]
            x.sum().backward()
            x, ind = x.data.sort()
            x = x.numpy()
            actual_grad = alphas.grad.data[ind].numpy()[:, 0]
            # Compare with expected gradient dx/dalpha0 along constant cdf(x,alpha).
            # This reduces to a distribution Beta(alpha[0], alpha[1] + alpha[2]).
            cdf = scipy.stats.beta.cdf
            pdf = scipy.stats.beta.pdf
            alpha, beta = a0, a1 + a2
            eps = 0.01 * alpha / (1.0 + np.sqrt(alpha))
            cdf_alpha = (cdf(x, alpha + eps, beta) - cdf(x, alpha - eps, beta)) / (2 * eps)
            cdf_x = pdf(x, alpha, beta)
            expected_grad = -cdf_alpha / cdf_x
            rel_error = np.abs(actual_grad - expected_grad) / (expected_grad + 1e-30)
            self.assertLess(np.max(rel_error), 0.001, '\n'.join([
                'Bad gradient dx[0]/dalpha[0] for Dirichlet([{}, {}, {}])'.format(a0, a1, a2),
                'x {}'.format(x),
                'expected {}'.format(expected_grad),
                'actual {}'.format(actual_grad),
                'rel error {}'.format(rel_error),
                'max error {}'.format(rel_error.max()),
                'at x={}'.format(x[rel_error.argmax()]),
            ]))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_beta_wrt_alpha(self):
        num_samples = 20
        grid = [1e-2, 1e-1, 1e0, 1e1, 1e2]
        for alpha, beta in product(grid, grid):
            alphas = Variable(torch.FloatTensor([alpha] * num_samples), requires_grad=True)
            betas = Variable(torch.FloatTensor([beta] * num_samples).type_as(alphas))
            x = Beta(alphas, betas).rsample()
            x.sum().backward()
            x, ind = x.data.sort()
            x = x.numpy()
            actual_grad = alphas.grad.data[ind].numpy()
            # Compare with expected gradient dx/dalpha along constant cdf(x,alpha,beta).
            cdf = scipy.stats.beta.cdf
            pdf = scipy.stats.beta.pdf
            eps = 0.01 * alpha / (1.0 + np.sqrt(alpha))
            cdf_alpha = (cdf(x, alpha + eps, beta) - cdf(x, alpha - eps, beta)) / (2 * eps)
            cdf_x = pdf(x, alpha, beta)
            expected_grad = -cdf_alpha / cdf_x
            rel_error = np.abs(actual_grad - expected_grad) / (expected_grad + 1e-30)
            self.assertLess(np.max(rel_error), 0.005, '\n'.join([
                'Bad gradient dx/dalpha for x ~ Beta({}, {})'.format(alpha, beta),
                'x {}'.format(x),
                'expected {}'.format(expected_grad),
                'actual {}'.format(actual_grad),
                'rel error {}'.format(rel_error),
                'max error {}'.format(rel_error.max()),
                'at x = {}'.format(x[rel_error.argmax()]),
            ]))

    @unittest.skipIf(not TEST_NUMPY, "NumPy not found")
    def test_beta_wrt_beta(self):
        num_samples = 20
        grid = [1e-2, 1e-1, 1e0, 1e1, 1e2]
        for alpha, beta in product(grid, grid):
            betas = Variable(torch.FloatTensor([beta] * num_samples), requires_grad=True)
            alphas = Variable(torch.FloatTensor([alpha] * num_samples).type_as(betas))
            x = Beta(alphas, betas).rsample()
            x.sum().backward()
            x, ind = x.data.sort()
            x = x.numpy()
            actual_grad = betas.grad.data[ind].numpy()
            # Compare with expected gradient dx/dbeta along constant cdf(x,alpha,beta).
            cdf = scipy.stats.beta.cdf
            pdf = scipy.stats.beta.pdf
            eps = 0.01 * beta / (1.0 + np.sqrt(beta))
            cdf_beta = (cdf(x, alpha, beta + eps) - cdf(x, alpha, beta - eps)) / (2 * eps)
            cdf_x = pdf(x, alpha, beta)
            expected_grad = -cdf_beta / cdf_x
            rel_error = np.abs(actual_grad - expected_grad) / (expected_grad + 1e-30)
            self.assertLess(np.max(rel_error), 0.005, '\n'.join([
                'Bad gradient dx/dbeta for x ~ Beta({}, {})'.format(alpha, beta),
                'x {}'.format(x),
                'expected {}'.format(expected_grad),
                'actual {}'.format(actual_grad),
                'rel error {}'.format(rel_error),
                'max error {}'.format(rel_error.max()),
                'at x = {!r}'.format(x[rel_error.argmax()]),
            ]))

    def test_dirichlet_multivariate(self):
        alpha_crit = 0.25 * (5.0 ** 0.5 - 1.0)
        num_samples = 100000
        for shift in [-0.1, -0.05, -0.01, 0.0, 0.01, 0.05, 0.10]:
            alpha = alpha_crit + shift
            alpha = Variable(torch.FloatTensor([alpha]), requires_grad=True)
            alpha_vec = torch.cat([alpha, alpha, alpha.new([1])])
            z = Dirichlet(alpha_vec.expand(num_samples, 3)).rsample()
            mean_z3 = 1.0 / (2.0 * alpha + 1.0)
            loss = torch.pow(z[:, 2] - mean_z3, 2.0).mean()
            actual_grad = grad(loss, [alpha])[0].data
            # Compute expected gradient by hand.
            num = 1.0 - 2.0 * alpha - 4.0 * alpha**2
            den = (1.0 + alpha)**2 * (1.0 + 2.0 * alpha)**3
            expected_grad = (num / den).data
            self.assertEqual(actual_grad, expected_grad, 0.002, '\n'.join([
                "alpha = alpha_c + %.2g" % shift,
                "expected_grad: %.5g" % expected_grad,
                "actual_grad: %.5g" % actual_grad,
                "error = %.2g" % torch.abs(expected_grad - actual_grad).max(),
            ]))

    def test_dirichlet_tangent_field(self):
        num_samples = 20
        alpha_grid = [0.5, 1.0, 2.0]

        # v = dx/dalpha[0] is the reparameterized gradient aka tangent field.
        def compute_v(x, alpha):
            return torch.stack([
                _Dirichlet_backward(x, alpha, torch.eye(3, 3)[i].expand_as(x))[:, 0]
                for i in range(3)
            ], dim=-1)

        for a1, a2, a3 in product(alpha_grid, alpha_grid, alpha_grid):
            alpha = Variable(torch.Tensor([a1, a2, a3]).expand(num_samples, 3), requires_grad=True)
            x = Dirichlet(alpha).rsample()
            dlogp_da = grad([Dirichlet(alpha).log_prob(x.detach()).sum()],
                            [alpha], retain_graph=True)[0].data[:, 0]
            dlogp_dx = grad([Dirichlet(alpha.detach()).log_prob(x).sum()],
                            [x], retain_graph=True)[0].data
            v = torch.stack([grad([x[:, i].sum()], [alpha], retain_graph=True)[0].data[:, 0]
                             for i in range(3)], dim=-1)
            # Compute ramaining properties by finite difference.
            x = x.data
            alpha = alpha.data
            self.assertEqual(compute_v(x, alpha), v, message='Bug in compute_v() helper')
            # dx is an arbitrary orthonormal basis tangent to the simplex.
            dx = torch.Tensor([[2, -1, -1], [0, 1, -1]])
            dx /= dx.norm(2, -1, True)
            eps = 1e-2 * x.min(-1, True)[0]  # avoid boundary
            dv0 = (compute_v(x + eps * dx[0], alpha) - compute_v(x - eps * dx[0], alpha)) / (2 * eps)
            dv1 = (compute_v(x + eps * dx[1], alpha) - compute_v(x - eps * dx[1], alpha)) / (2 * eps)
            div_v = (dv0 * dx[0] + dv1 * dx[1]).sum(-1)
            # This is a modification of the standard continuity equation, using the product rule to allow
            # expression in terms of log_prob rather than the less numerically stable log_prob.exp().
            error = dlogp_da + (dlogp_dx * v).sum(-1) + div_v
            self.assertLess(torch.abs(error).max(), 0.005, '\n'.join([
                'Dirichlet([{}, {}, {}]) gradient violates continuity equation:'.format(a1, a2, a3),
                'error = {}'.format(error),
            ]))


class TestDistributionShapes(TestCase):
    def setUp(self):
        super(TestCase, self).setUp()
        self.scalar_sample = 1
        self.tensor_sample_1 = torch.ones(3, 2)
        self.tensor_sample_2 = torch.ones(3, 2, 3)

    def test_entropy_shape(self):
        for Dist, params in EXAMPLES:
            for i, param in enumerate(params):
                dist = Dist(**param)
                try:
                    actual_shape = dist.entropy().size()
                    expected_shape = dist._batch_shape
                    if not expected_shape:
                        expected_shape = torch.Size((1,))  # TODO Remove this once scalars are supported.
                    message = '{} example {}/{}, shape mismatch. expected {}, actual {}'.format(
                        Dist.__name__, i, len(params), expected_shape, actual_shape)
                    self.assertEqual(actual_shape, expected_shape, message=message)
                except NotImplementedError:
                    continue

    def test_bernoulli_shape_scalar_params(self):
        bernoulli = Bernoulli(0.3)
        self.assertEqual(bernoulli._batch_shape, torch.Size())
        self.assertEqual(bernoulli._event_shape, torch.Size())
        self.assertEqual(bernoulli.sample().size(), torch.Size((1,)))
        self.assertEqual(bernoulli.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, bernoulli.log_prob, self.scalar_sample)
        self.assertEqual(bernoulli.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(bernoulli.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_bernoulli_shape_tensor_params(self):
        bernoulli = Bernoulli(torch.Tensor([[0.6, 0.3], [0.6, 0.3], [0.6, 0.3]]))
        self.assertEqual(bernoulli._batch_shape, torch.Size((3, 2)))
        self.assertEqual(bernoulli._event_shape, torch.Size(()))
        self.assertEqual(bernoulli.sample().size(), torch.Size((3, 2)))
        self.assertEqual(bernoulli.sample((3, 2)).size(), torch.Size((3, 2, 3, 2)))
        self.assertEqual(bernoulli.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, bernoulli.log_prob, self.tensor_sample_2)
        self.assertEqual(bernoulli.log_prob(torch.ones(3, 1, 1)).size(), torch.Size((3, 3, 2)))

    def test_beta_shape_scalar_params(self):
        dist = Beta(0.1, 0.1)
        self.assertEqual(dist._batch_shape, torch.Size())
        self.assertEqual(dist._event_shape, torch.Size())
        self.assertEqual(dist.sample().size(), torch.Size((1,)))
        self.assertEqual(dist.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, dist.log_prob, self.scalar_sample)
        self.assertEqual(dist.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(dist.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_beta_shape_tensor_params(self):
        dist = Beta(torch.Tensor([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]),
                    torch.Tensor([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]))
        self.assertEqual(dist._batch_shape, torch.Size((3, 2)))
        self.assertEqual(dist._event_shape, torch.Size(()))
        self.assertEqual(dist.sample().size(), torch.Size((3, 2)))
        self.assertEqual(dist.sample((3, 2)).size(), torch.Size((3, 2, 3, 2)))
        self.assertEqual(dist.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, dist.log_prob, self.tensor_sample_2)
        self.assertEqual(dist.log_prob(torch.ones(3, 1, 1)).size(), torch.Size((3, 3, 2)))

    def test_multinomial_shape(self):
        dist = Multinomial(10, torch.Tensor([[0.6, 0.3], [0.6, 0.3], [0.6, 0.3]]))
        self.assertEqual(dist._batch_shape, torch.Size((3,)))
        self.assertEqual(dist._event_shape, torch.Size((2,)))
        self.assertEqual(dist.sample().size(), torch.Size((3, 2)))
        self.assertEqual(dist.sample((3, 2)).size(), torch.Size((3, 2, 3, 2)))
        self.assertEqual(dist.log_prob(self.tensor_sample_1).size(), torch.Size((3,)))
        self.assertRaises(ValueError, dist.log_prob, self.tensor_sample_2)
        self.assertEqual(dist.log_prob(torch.ones(3, 1, 2)).size(), torch.Size((3, 3)))

    def test_categorical_shape(self):
        dist = Categorical(torch.Tensor([[0.6, 0.3], [0.6, 0.3], [0.6, 0.3]]))
        self.assertEqual(dist._batch_shape, torch.Size((3,)))
        self.assertEqual(dist._event_shape, torch.Size(()))
        self.assertEqual(dist.sample().size(), torch.Size((3,)))
        self.assertEqual(dist.sample((3, 2)).size(), torch.Size((3, 2, 3,)))
        self.assertRaises(ValueError, dist.log_prob, self.tensor_sample_1)
        self.assertEqual(dist.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))
        self.assertEqual(dist.log_prob(torch.ones(3, 1)).size(), torch.Size((3, 3)))

    def test_one_hot_categorical_shape(self):
        dist = OneHotCategorical(torch.Tensor([[0.6, 0.3], [0.6, 0.3], [0.6, 0.3]]))
        self.assertEqual(dist._batch_shape, torch.Size((3,)))
        self.assertEqual(dist._event_shape, torch.Size((2,)))
        self.assertEqual(dist.sample().size(), torch.Size((3, 2)))
        self.assertEqual(dist.sample((3, 2)).size(), torch.Size((3, 2, 3, 2)))
        self.assertEqual(dist.log_prob(self.tensor_sample_1).size(), torch.Size((3,)))
        self.assertRaises(ValueError, dist.log_prob, self.tensor_sample_2)
        self.assertEqual(dist.log_prob(dist.enumerate_support()).size(), torch.Size((2, 3)))
        self.assertEqual(dist.log_prob(torch.ones((3, 1, 2))).size(), torch.Size((3, 3)))

    def test_cauchy_shape_scalar_params(self):
        cauchy = Cauchy(0, 1)
        self.assertEqual(cauchy._batch_shape, torch.Size())
        self.assertEqual(cauchy._event_shape, torch.Size())
        self.assertEqual(cauchy.sample().size(), torch.Size((1,)))
        self.assertEqual(cauchy.sample(torch.Size((3, 2))).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, cauchy.log_prob, self.scalar_sample)
        self.assertEqual(cauchy.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(cauchy.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_cauchy_shape_tensor_params(self):
        cauchy = Cauchy(torch.Tensor([0, 0]), torch.Tensor([1, 1]))
        self.assertEqual(cauchy._batch_shape, torch.Size((2,)))
        self.assertEqual(cauchy._event_shape, torch.Size(()))
        self.assertEqual(cauchy.sample().size(), torch.Size((2,)))
        self.assertEqual(cauchy.sample(torch.Size((3, 2))).size(), torch.Size((3, 2, 2)))
        self.assertEqual(cauchy.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, cauchy.log_prob, self.tensor_sample_2)
        self.assertEqual(cauchy.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))

    def test_dirichlet_shape(self):
        dist = Dirichlet(torch.Tensor([[0.6, 0.3], [1.6, 1.3], [2.6, 2.3]]))
        self.assertEqual(dist._batch_shape, torch.Size((3,)))
        self.assertEqual(dist._event_shape, torch.Size((2,)))
        self.assertEqual(dist.sample().size(), torch.Size((3, 2)))
        self.assertEqual(dist.sample((5, 4)).size(), torch.Size((5, 4, 3, 2)))
        self.assertEqual(dist.log_prob(self.tensor_sample_1).size(), torch.Size((3,)))
        self.assertRaises(ValueError, dist.log_prob, self.tensor_sample_2)
        self.assertEqual(dist.log_prob(torch.ones((3, 1, 2))).size(), torch.Size((3, 3)))

    def test_gamma_shape_scalar_params(self):
        gamma = Gamma(1, 1)
        self.assertEqual(gamma._batch_shape, torch.Size())
        self.assertEqual(gamma._event_shape, torch.Size())
        self.assertEqual(gamma.sample().size(), torch.Size((1,)))
        self.assertEqual(gamma.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, gamma.log_prob, self.scalar_sample)
        self.assertEqual(gamma.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(gamma.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_gamma_shape_tensor_params(self):
        gamma = Gamma(torch.Tensor([1, 1]), torch.Tensor([1, 1]))
        self.assertEqual(gamma._batch_shape, torch.Size((2,)))
        self.assertEqual(gamma._event_shape, torch.Size(()))
        self.assertEqual(gamma.sample().size(), torch.Size((2,)))
        self.assertEqual(gamma.sample((3, 2)).size(), torch.Size((3, 2, 2)))
        self.assertEqual(gamma.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, gamma.log_prob, self.tensor_sample_2)
        self.assertEqual(gamma.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))

    def test_chi2_shape_scalar_params(self):
        chi2 = Chi2(1)
        self.assertEqual(chi2._batch_shape, torch.Size())
        self.assertEqual(chi2._event_shape, torch.Size())
        self.assertEqual(chi2.sample().size(), torch.Size((1,)))
        self.assertEqual(chi2.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, chi2.log_prob, self.scalar_sample)
        self.assertEqual(chi2.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(chi2.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_chi2_shape_tensor_params(self):
        chi2 = Chi2(torch.Tensor([1, 1]))
        self.assertEqual(chi2._batch_shape, torch.Size((2,)))
        self.assertEqual(chi2._event_shape, torch.Size(()))
        self.assertEqual(chi2.sample().size(), torch.Size((2,)))
        self.assertEqual(chi2.sample((3, 2)).size(), torch.Size((3, 2, 2)))
        self.assertEqual(chi2.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, chi2.log_prob, self.tensor_sample_2)
        self.assertEqual(chi2.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))

    def test_studentT_shape_scalar_params(self):
        st = StudentT(1)
        self.assertEqual(st._batch_shape, torch.Size())
        self.assertEqual(st._event_shape, torch.Size())
        self.assertEqual(st.sample().size(), torch.Size((1,)))
        self.assertEqual(st.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, st.log_prob, self.scalar_sample)
        self.assertEqual(st.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(st.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_studentT_shape_tensor_params(self):
        st = StudentT(torch.Tensor([1, 1]))
        self.assertEqual(st._batch_shape, torch.Size((2,)))
        self.assertEqual(st._event_shape, torch.Size(()))
        self.assertEqual(st.sample().size(), torch.Size((2,)))
        self.assertEqual(st.sample((3, 2)).size(), torch.Size((3, 2, 2)))
        self.assertEqual(st.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, st.log_prob, self.tensor_sample_2)
        self.assertEqual(st.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))

    def test_pareto_shape_scalar_params(self):
        pareto = Pareto(1, 1)
        self.assertEqual(pareto._batch_shape, torch.Size())
        self.assertEqual(pareto._event_shape, torch.Size())
        self.assertEqual(pareto.sample().size(), torch.Size((1,)))
        self.assertEqual(pareto.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, pareto.log_prob, self.scalar_sample)
        self.assertEqual(pareto.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(pareto.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_normal_shape_scalar_params(self):
        normal = Normal(0, 1)
        self.assertEqual(normal._batch_shape, torch.Size())
        self.assertEqual(normal._event_shape, torch.Size())
        self.assertEqual(normal.sample().size(), torch.Size((1,)))
        self.assertEqual(normal.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, normal.log_prob, self.scalar_sample)
        self.assertEqual(normal.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(normal.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_normal_shape_tensor_params(self):
        normal = Normal(torch.Tensor([0, 0]), torch.Tensor([1, 1]))
        self.assertEqual(normal._batch_shape, torch.Size((2,)))
        self.assertEqual(normal._event_shape, torch.Size(()))
        self.assertEqual(normal.sample().size(), torch.Size((2,)))
        self.assertEqual(normal.sample((3, 2)).size(), torch.Size((3, 2, 2)))
        self.assertEqual(normal.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, normal.log_prob, self.tensor_sample_2)
        self.assertEqual(normal.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))

    def test_uniform_shape_scalar_params(self):
        uniform = Uniform(0, 1)
        self.assertEqual(uniform._batch_shape, torch.Size())
        self.assertEqual(uniform._event_shape, torch.Size())
        self.assertEqual(uniform.sample().size(), torch.Size((1,)))
        self.assertEqual(uniform.sample(torch.Size((3, 2))).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, uniform.log_prob, self.scalar_sample)
        self.assertEqual(uniform.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(uniform.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_uniform_shape_tensor_params(self):
        uniform = Uniform(torch.Tensor([0, 0]), torch.Tensor([1, 1]))
        self.assertEqual(uniform._batch_shape, torch.Size((2,)))
        self.assertEqual(uniform._event_shape, torch.Size(()))
        self.assertEqual(uniform.sample().size(), torch.Size((2,)))
        self.assertEqual(uniform.sample(torch.Size((3, 2))).size(), torch.Size((3, 2, 2)))
        self.assertEqual(uniform.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, uniform.log_prob, self.tensor_sample_2)
        self.assertEqual(uniform.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))

    def test_exponential_shape_scalar_param(self):
        expon = Exponential(1.)
        self.assertEqual(expon._batch_shape, torch.Size())
        self.assertEqual(expon._event_shape, torch.Size())
        self.assertEqual(expon.sample().size(), torch.Size((1,)))
        self.assertEqual(expon.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, expon.log_prob, self.scalar_sample)
        self.assertEqual(expon.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(expon.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_exponential_shape_tensor_param(self):
        expon = Exponential(torch.Tensor([1, 1]))
        self.assertEqual(expon._batch_shape, torch.Size((2,)))
        self.assertEqual(expon._event_shape, torch.Size(()))
        self.assertEqual(expon.sample().size(), torch.Size((2,)))
        self.assertEqual(expon.sample((3, 2)).size(), torch.Size((3, 2, 2)))
        self.assertEqual(expon.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, expon.log_prob, self.tensor_sample_2)
        self.assertEqual(expon.log_prob(torch.ones(2, 2)).size(), torch.Size((2, 2)))

    def test_laplace_shape_scalar_params(self):
        laplace = Laplace(0, 1)
        self.assertEqual(laplace._batch_shape, torch.Size())
        self.assertEqual(laplace._event_shape, torch.Size())
        self.assertEqual(laplace.sample().size(), torch.Size((1,)))
        self.assertEqual(laplace.sample((3, 2)).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, laplace.log_prob, self.scalar_sample)
        self.assertEqual(laplace.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertEqual(laplace.log_prob(self.tensor_sample_2).size(), torch.Size((3, 2, 3)))

    def test_laplace_shape_tensor_params(self):
        laplace = Laplace(torch.Tensor([0, 0]), torch.Tensor([1, 1]))
        self.assertEqual(laplace._batch_shape, torch.Size((2,)))
        self.assertEqual(laplace._event_shape, torch.Size(()))
        self.assertEqual(laplace.sample().size(), torch.Size((2,)))
        self.assertEqual(laplace.sample((3, 2)).size(), torch.Size((3, 2, 2)))
        self.assertEqual(laplace.log_prob(self.tensor_sample_1).size(), torch.Size((3, 2)))
        self.assertRaises(ValueError, laplace.log_prob, self.tensor_sample_2)
        self.assertEqual(laplace.log_prob(torch.ones(2, 1)).size(), torch.Size((2, 2)))


class TestKL(TestCase):
    def setUp(self):
        self.finite_examples = [
            (Bernoulli(0.7), Bernoulli(0.3)),
            (Beta(1, 2), Beta(3, 4)),
            (Beta(1, 2), Chi2(3)),
            (Beta(1, 2), Exponential(3)),
            (Beta(1, 2), Gamma(3, 4)),
            (Beta(1, 2), Normal(-3, 4)),
            (Chi2(2), Chi2(3)),
            (Chi2(2), Gamma(3, 4)),
            (Chi2(2), Exponential(3)),
            (Dirichlet(torch.Tensor([1, 2])), Dirichlet(torch.Tensor([3, 4]))),
            (Exponential(1), Chi2(2)),
            (Exponential(1), Exponential(2)),
            (Exponential(1), Gamma(2, 3)),
            (Exponential(1), Gumbel(-2, 3)),
            (Exponential(2), Normal(-3, 4)),
            (Gamma(1, 2), Chi2(3)),
            (Gamma(1, 2), Exponential(3)),
            (Gamma(1, 2), Gamma(3, 4)),
            (Gamma(1, 2), Gumbel(-3, 4)),
            (Gumbel(-1, 2), Gumbel(-3, 4)),
            (Gumbel(-1, 2), Normal(-3, 4)),  # This case fails for n <= 22000
            (Laplace(1, 2), Laplace(-3, 4)),
            (Laplace(-1, 2), Normal(-3, 4)),
            (Normal(-1, 2), Gumbel(-3, 4)),
            (Normal(1, 2), Normal(-3, 4)),
            (Pareto(1, 2), Chi2(3)),
            (Pareto(1, 2), Exponential(3)),  # This case fails for n <= 22000
            (Pareto(1, 2), Gamma(3, 4)),  # This case fails for n <= 22000
            (Pareto(1, 2), Laplace(-3, 4)),
            (Pareto(1, 2), Laplace(3, 4)),
            (Pareto(1, 3), Normal(-2, 4)),
            (Uniform(0.25, 0.75), Beta(3, 4)),
            (Uniform(1, 2), Chi2(3)),
            (Uniform(1, 2), Exponential(3)),
            (Uniform(1, 2), Gamma(3, 4)),
            (Uniform(-1, 2), Gumbel(-3, 4)),
            (Uniform(-1, 2), Normal(-3, 4)),
            (Uniform(2, 3), Pareto(1, 4))
        ]

        self.infinite_examples = [
            (Beta(1, 2), Uniform(0.25, 1)),
            (Beta(1, 2), Uniform(0, 0.75)),
            (Beta(1, 2), Uniform(0.25, 0.75)),
            (Beta(1, 2), Pareto(1, 2)),
            (Chi2(1), Beta(2, 3)),
            (Chi2(1), Pareto(2, 3)),
            (Chi2(1), Uniform(-2, 3)),
            (Exponential(1), Beta(2, 3)),
            (Exponential(1), Pareto(2, 3)),
            (Exponential(1), Uniform(-2, 3)),
            (Gamma(1, 2), Beta(3, 4)),
            (Gamma(1, 2), Pareto(3, 4)),
            (Gamma(1, 2), Uniform(-3, 4)),
            (Gumbel(-1, 2), Beta(3, 4)),
            (Gumbel(-1, 2), Chi2(3)),
            (Gumbel(-1, 2), Exponential(3)),
            (Gumbel(-1, 2), Gamma(3, 4)),
            (Gumbel(-1, 2), Pareto(3, 4)),
            (Gumbel(-1, 2), Uniform(-3, 4)),
            (Laplace(-1, 2), Beta(3, 4)),
            (Laplace(-1, 2), Chi2(3)),
            (Laplace(-1, 2), Exponential(3)),
            (Laplace(-1, 2), Gamma(3, 4)),
            (Laplace(-1, 2), Pareto(3, 4)),
            (Laplace(-1, 2), Uniform(-3, 4)),
            (Normal(-1, 2), Beta(3, 4)),
            (Normal(-1, 2), Chi2(3)),
            (Normal(-1, 2), Exponential(3)),
            (Normal(-1, 2), Gamma(3, 4)),
            (Normal(-1, 2), Pareto(3, 4)),
            (Normal(-1, 2), Uniform(-3, 4)),
            (Pareto(2, 1), Chi2(3)),
            (Pareto(2, 1), Exponential(3)),
            (Pareto(2, 1), Gamma(3, 4)),
            (Pareto(1, 2), Normal(-3, 4)),
            (Pareto(1, 2), Pareto(3, 4)),
            (Uniform(-1, 1), Beta(2, 2)),
            (Uniform(0, 2), Beta(3, 4)),
            (Uniform(-1, 2), Beta(3, 4)),
            (Uniform(-1, 2), Chi2(3)),
            (Uniform(-1, 2), Exponential(3)),
            (Uniform(-1, 2), Gamma(3, 4)),
            (Uniform(-1, 2), Pareto(3, 4)),
        ]

    def test_kl_monte_carlo(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        for p, q in self.finite_examples:
            x = p.sample(sample_shape=(23000,))
            expected = (p.log_prob(x) - q.log_prob(x)).mean(0)
            actual = kl_divergence(p, q)
            message = 'Incorrect KL({}, {}). expected {}, actual {}'.format(
                type(p).__name__, type(q).__name__, expected, actual)
            self.assertEqual(expected, actual, prec=0.1, message=message)

    def test_kl_infinite(self):
        for p, q in self.infinite_examples:
            self.assertTrue((kl_divergence(p, q) == float('inf')).all(),
                            'Incorrect KL({}, {})'.format(type(p).__name__, type(q).__name__))

    def test_entropy_monte_carlo(self):
        set_rng_seed(0)  # see Note [Randomized statistical tests]
        for Dist, params in EXAMPLES:
            for i, param in enumerate(params):
                dist = Dist(**param)
                try:
                    actual = dist.entropy()
                except NotImplementedError:
                    continue
                x = dist.sample(sample_shape=(20000,))
                expected = -dist.log_prob(x).mean(0)
                if isinstance(actual, Variable):
                    actual = actual.data
                    expected = expected.data
                ignore = (expected == float('inf'))
                expected[ignore] = actual[ignore]
                self.assertEqual(actual, expected, prec=0.2, message='\n'.join([
                    '{} example {}/{}, incorrect .entropy().'.format(Dist.__name__, i, len(params)),
                    'Expected (monte carlo) {}'.format(expected),
                    'Actual (analytic) {}'.format(actual),
                    'max error = {}'.format(torch.abs(actual - expected).max()),
                ]))


class TestConstraints(TestCase):
    def test_params_contains(self):
        for Dist, params in EXAMPLES:
            for i, param in enumerate(params):
                dist = Dist(**param)
                for name, value in param.items():
                    if not (torch.is_tensor(value) or isinstance(value, Variable)):
                        value = torch.Tensor([value])
                    if Dist in (Categorical, OneHotCategorical, Multinomial) and name == 'probs':
                        # These distributions accept positive probs, but elsewhere we
                        # use a stricter constraint to the simplex.
                        value = value / value.sum(-1, True)
                    try:
                        constraint = dist.params[name]
                    except KeyError:
                        continue  # ignore optional parameters
                    if is_dependent(constraint):
                        continue
                    message = '{} example {}/{} parameter {} = {}'.format(
                        Dist.__name__, i, len(params), name, value)
                    self.assertTrue(constraint.check(value).all(), msg=message)

    def test_support_contains(self):
        for Dist, params in EXAMPLES:
            self.assertIsInstance(Dist.support, Constraint)
            for i, param in enumerate(params):
                dist = Dist(**param)
                value = dist.sample()
                constraint = dist.support
                message = '{} example {}/{} sample = {}'.format(
                    Dist.__name__, i, len(params), value)
                self.assertTrue(constraint.check(value).all(), msg=message)


class TestNumericalStability(TestCase):
    def _test_pdf_score(self,
                        dist_class,
                        x,
                        expected_value,
                        probs=None,
                        logits=None,
                        expected_gradient=None,
                        prec=1e-5):
        if probs is not None:
            p = Variable(probs, requires_grad=True)
            dist = dist_class(p)
        else:
            p = Variable(logits, requires_grad=True)
            dist = dist_class(logits=p)
        log_pdf = dist.log_prob(Variable(x))
        log_pdf.sum().backward()
        self.assertEqual(log_pdf.data,
                         expected_value,
                         prec=prec,
                         message='Incorrect value for tensor type: {}. Expected = {}, Actual = {}'
                         .format(type(x), expected_value, log_pdf.data))
        if expected_gradient is not None:
            self.assertEqual(p.grad.data,
                             expected_gradient,
                             prec=prec,
                             message='Incorrect gradient for tensor type: {}. Expected = {}, Actual = {}'
                             .format(type(x), expected_gradient, p.grad.data))

    def test_bernoulli_gradient(self):
        for tensor_type in [torch.FloatTensor, torch.DoubleTensor]:
            self._test_pdf_score(dist_class=Bernoulli,
                                 probs=tensor_type([0]),
                                 x=tensor_type([0]),
                                 expected_value=tensor_type([0]),
                                 expected_gradient=tensor_type([0]))

            self._test_pdf_score(dist_class=Bernoulli,
                                 probs=tensor_type([0]),
                                 x=tensor_type([1]),
                                 expected_value=tensor_type([_finfo(tensor_type([])).eps]).log(),
                                 expected_gradient=tensor_type([0]))

            self._test_pdf_score(dist_class=Bernoulli,
                                 probs=tensor_type([1e-4]),
                                 x=tensor_type([1]),
                                 expected_value=tensor_type([math.log(1e-4)]),
                                 expected_gradient=tensor_type([10000]))

            # Lower precision due to:
            # >>> 1 / (1 - torch.FloatTensor([0.9999]))
            # 9998.3408
            # [torch.FloatTensor of size 1]
            self._test_pdf_score(dist_class=Bernoulli,
                                 probs=tensor_type([1 - 1e-4]),
                                 x=tensor_type([0]),
                                 expected_value=tensor_type([math.log(1e-4)]),
                                 expected_gradient=tensor_type([-10000]),
                                 prec=2)

            self._test_pdf_score(dist_class=Bernoulli,
                                 logits=tensor_type([math.log(9999)]),
                                 x=tensor_type([0]),
                                 expected_value=tensor_type([math.log(1e-4)]),
                                 expected_gradient=tensor_type([-1]),
                                 prec=1e-3)

    def test_bernoulli_with_logits_underflow(self):
        for tensor_type, lim in ([(torch.FloatTensor, -1e38),
                                  (torch.DoubleTensor, -1e308)]):
            self._test_pdf_score(dist_class=Bernoulli,
                                 logits=tensor_type([lim]),
                                 x=tensor_type([0]),
                                 expected_value=tensor_type([0]),
                                 expected_gradient=tensor_type([0]))

    def test_bernoulli_with_logits_overflow(self):
        for tensor_type, lim in ([(torch.FloatTensor, 1e38),
                                  (torch.DoubleTensor, 1e308)]):
            self._test_pdf_score(dist_class=Bernoulli,
                                 logits=tensor_type([lim]),
                                 x=tensor_type([1]),
                                 expected_value=tensor_type([0]),
                                 expected_gradient=tensor_type([0]))

    def test_categorical_log_prob(self):
        for tensor_type in ([torch.FloatTensor, torch.DoubleTensor]):
            p = Variable(tensor_type([0, 1]), requires_grad=True)
            categorical = OneHotCategorical(p)
            log_pdf = categorical.log_prob(Variable(tensor_type([0, 1])))
            self.assertEqual(log_pdf.data[0], 0)

    def test_categorical_log_prob_with_logits(self):
        for tensor_type in ([torch.FloatTensor, torch.DoubleTensor]):
            p = Variable(tensor_type([-float('inf'), 0]), requires_grad=True)
            categorical = OneHotCategorical(logits=p)
            log_pdf_prob_1 = categorical.log_prob(Variable(tensor_type([0, 1])))
            self.assertEqual(log_pdf_prob_1.data[0], 0)
            log_pdf_prob_0 = categorical.log_prob(Variable(tensor_type([1, 0])))
            self.assertEqual(log_pdf_prob_0.data[0], -float('inf'), allow_inf=True)

    def test_multinomial_log_prob(self):
        for tensor_type in [torch.FloatTensor, torch.DoubleTensor]:
            p = Variable(tensor_type([0, 1]), requires_grad=True)
            s = Variable(tensor_type([0, 10]))
            multinomial = Multinomial(10, p)
            log_pdf = multinomial.log_prob(s)
            self.assertEqual(log_pdf.data[0], 0)

    def test_multinomial_log_prob_with_logits(self):
        for tensor_type in [torch.FloatTensor, torch.DoubleTensor]:
            p = Variable(tensor_type([-float('inf'), 0]), requires_grad=True)
            multinomial = Multinomial(10, logits=p)
            log_pdf_prob_1 = multinomial.log_prob(Variable(tensor_type([0, 10])))
            self.assertEqual(log_pdf_prob_1.data[0], 0)
            log_pdf_prob_0 = multinomial.log_prob(Variable(tensor_type([10, 0])))
            self.assertEqual(log_pdf_prob_0.data[0], -float('inf'), allow_inf=True)


if __name__ == '__main__':
    run_tests()
