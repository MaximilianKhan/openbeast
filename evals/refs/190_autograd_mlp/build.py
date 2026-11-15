#!/usr/bin/env python3
"""Regenerate evals/tasks/190_autograd_mlp.json from the sources here.

  oracle.py          forward-only evaluator, seeded program generator,
                     central finite differences, hand-backprop MLP oracle;
                     embedded in the validator only
  driver.py          runs inside the solution's own `python3 -I` process:
                     executes the hidden programs with the solution's
                     Tensor and reports values/shapes/grads (no expected
                     values in it)
  validator_main.py  validator body: lint, build cases, run the driver
                     under a 10 s limit, compare, print only case index+kind
  sample_test.py     the agent-visible sample (the only fixture file)
  autograd.py        reference solution
  wrong_*.py / slow.py / leak.py   prover arms, classes in prove.json

Run:  python3 evals/refs/190_autograd_mlp/build.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
OUT = os.path.join(REPO, 'evals', 'tasks', '190_autograd_mlp.json')
DIR = '/tmp/eval_autograd190'


def read(n):
    with open(os.path.join(HERE, n)) as f:
        return f.read()


TASK = f"""\
Create {DIR}/autograd.py: a reverse-mode automatic-differentiation library over N-dimensional float tensors, plus a small MLP trainer built on it. Python 3 standard library only: the module is imported by `python3 -I` with {DIR} as the working directory, so numpy, torch and every other third-party package are unavailable (and importing them, ctypes, or importlib is rejected).

The module must define:

class Tensor
- Tensor(data, requires_grad=False): data is a Python int/float (a rank-0 tensor) or a rectangular nested list of numbers (rank = nesting depth). Values are stored as floats.
- t.shape: tuple of ints, () for rank 0.
- t.data: the values as nested lists of floats laid out by t.shape; a plain float for rank 0.
- t.requires_grad: bool. The result of an operation requires grad iff at least one Tensor operand does.
- t.grad: None, or the accumulated gradient with exactly the layout of t.data (a float for rank 0). Only leaves (tensors made by the constructor) with requires_grad=True receive gradients; a leaf with requires_grad=False always keeps grad None. A requires_grad leaf that no root depends on may keep None. Gradients of non-leaf tensors are not checked.

Operations. Each returns a new Tensor and never modifies its inputs:
- a + b, a - b, a * b, a / b: elementwise with NumPy broadcasting. Either operand may be a Python int/float, on either side (2.0 - t, 3 / t, 0.5 * t).
- -a, and a ** p with p a Python int/float (the base is positive whenever p is not an integer).
- a @ b: NumPy matmul semantics for operands of rank >= 1. A rank-1 left operand acts as a (1, n) row and a rank-1 right operand as an (n, 1) column; that inserted axis is removed from the result (so rank-1 @ rank-1 is rank 0). Axes before the last two are batch axes and broadcast against each other.
- a.exp(), a.log(), a.tanh(), a.relu(). The relu derivative at 0 is 0.
- a.sum(axis=None, keepdims=False), a.mean(axis=None, keepdims=False), a.max(axis=None, keepdims=False), a.logsumexp(axis=None, keepdims=False). axis is None (all axes), an int, or a tuple of ints; negative axes count from the end; keepdims=True keeps each reduced axis with size 1. These are NumPy semantics. The gradient of max goes to the maximal element; the hidden tests contain no ties. logsumexp(x) = log(sum(exp(x))) must be finite and accurate when the inputs have magnitude up to 1000.
- a.reshape(shape): shape is a tuple of ints, at most one of them -1; row-major element order.
- a.T: the tensor with the order of all its axes reversed (rank 0 and rank 1 are returned unchanged in value and shape).
- t.backward(): t must have rank 0. For every requires_grad leaf, adds d t / d leaf to leaf.grad (setting it if it is None). backward() may be called on several roots in turn, including the same root twice and roots that share intermediate tensors: afterwards each leaf's grad must equal the sum, over those calls, of the derivative of that call's root. Graphs can be 20,000 operations deep, and a tensor can feed hundreds of later operations.

cross_entropy(logits, targets): logits is a Tensor of shape (N, C), targets a list of N ints in [0, C). Returns the rank-0 Tensor equal to the mean over n of ( logsumexp(logits[n, :]) - logits[n, targets[n]] ), differentiable with respect to logits, finite and accurate for logits of magnitude up to 1000.

train_mlp(X, y, params, lr, steps) -> (losses, final_params). X is an N x D nested list of floats, y a list of N ints, params = [W1, b1, W2, b2, W3, b3] as nested lists: W1 is D x H1, b1 has length H1, W2 is H1 x H2, b2 has length H2, W3 is H2 x C, b3 has length C. The model is logits = relu(tanh(X @ W1 + b1) @ W2 + b2) @ W3 + b3 and the loss is cross_entropy(logits, y). Run `steps` iterations of full-batch gradient descent using gradients computed by your Tensor: each iteration evaluates the loss at the current parameters, then replaces every parameter p by p - lr * dloss/dp (all six from the same gradient evaluation). losses is a list of steps + 1 floats: the loss before each update, then the loss at the final parameters. final_params is a list of the six final parameters as nested lists in the layout of params. Do not modify the input lists.

Validation imports your module in a fresh process and runs about 200 hidden, seeded programs built from the operations above (random shapes up to rank 3, broadcasting, every reduction form, matmul of ranks 1 to 3, shared subgraphs, several backward() calls, very large logits, a 20,000-operation chain), then one 60-step train_mlp run on a 60-point, 3-class problem. Every forward value must match to a relative 1e-9; every leaf gradient g must satisfy |g - fd| <= 1e-6 * max(1, |fd|), where fd is a central finite difference of the root(s); the MLP loss trace and final parameters must match a reference implementation to a relative 1e-6, and the final MLP loss must be below half the initial one. The whole hidden run (programs and training) must finish within 10 seconds. Only the index and kind of the first failing case is reported, so write your own tests. {DIR}/sample_test.py is a small example (run it with python3 from {DIR}).
"""

validator = ('ORACLE = ' + repr(read('oracle.py')) + '\n' +
             'DRIVER = ' + repr(read('driver.py')) + '\n' + read('validator_main.py'))

setup = (f"rm -rf {DIR} && mkdir -p {DIR} && cat > {DIR}/sample_test.py <<'SAMPLE_EOF'\n"
         + read('sample_test.py') + "SAMPLE_EOF\n")

task = {
    'id': '190_autograd_mlp',
    'name': 'Reverse-mode autograd over N-d tensors + small MLP',
    'difficulty': 'hard',
    'category': 'LLM / ML',
    'subcategory': 'Autodiff',
    'max_iter': 25,
    'est_s': 165,
    'max_wall_s': 900,
    'role': 'scored',
    'ref_stem': '190_autograd_mlp/autograd',
    'dest': f'{DIR}/autograd.py',
    'setup': setup,
    'task': TASK,
    'validation': {'type': 'python', 'script': validator},
    'cleanup': f'rm -rf {DIR}',
}

with open(OUT, 'w') as f:
    json.dump(task, f, indent=1)
    f.write('\n')
print('wrote', OUT, len(validator), 'bytes of validator')
