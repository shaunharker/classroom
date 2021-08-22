import torch
from torch.cuda.amp import autocast
import numpy as np
import copy
import random
from time import time
from ..dataset.utf8 import utf8encode
from ..dataset.utf8 import utf8decode
from ..dataset import BytesDataset


class Student:
    """
    Encapsulates `model`, `optimizer`, `dataset`, `batch_size`, `example_length` for the purposes of training.
    Stores training metrics (`time`, `times`, `grades`) generated by calls to `study`.
    ### Notes:
    * `save` and `load` serialize to and from disk
    * `push` and `pop` serialize to and from an N-deep stack (implemented through the `self.parent` reference) where currently N is set to 1 (e.g. two `push`s in a row loses the first `push`)
    * `clone` creates a clone which is a deepcopy except for `self.parent`, which is not a copy.
    * `mutate` is experimental in value at this point
    """
    def __init__(self, model=None, optimizer=None, dataset=None, batch_size=None, example_length=None):
        self.model = model
        self.optimizer = optimizer
        self.dataset = dataset
        self.batch_size = batch_size
        self.example_length = example_length

        self.time = 0.0
        self.times = []
        self.grades = []

        self.parent = None

        self.baseline = None
        self.baseline_grades = []
        self.relative_grades = []

    @staticmethod
    def load_from_path(path):
        """
        Load the `Student` object stored at `path` and return it.
        """
        student = Student()
        student.load(path)
        return student

    def load(self, path):
        """
        Load the `Student` object stored at `path` into `self`.
        """
        checkpoint = torch.load(path)
        self.model = checkpoint["model"]
        self.optimizer = checkpoint["optimizer"]
        self.dataset = checkpoint["dataset"]
        self.batch_size = checkpoint["batch_size"]
        self.example_length = checkpoint["example_length"]
        self.time = checkpoint["time"]
        self.times = checkpoint["times"]
        self.grades = checkpoint["grades"]
        self.parent = checkpoint["parent"]
        self.baseline = checkpoint["baseline"]
        self.baseline_grades = checkpoint["baseline_grades"]
        self.relative_grades = checkpoint["relative_grades"]

    def save(self, path):
        """
        Save `self` to `path`.
        """
        checkpoint = {
            "model": self.model,
            "optimizer": self.optimizer,
            "dataset": self.dataset,
            "batch_size": self.batch_size,
            "example_length": self.example_length,
            "time": self.time,
            "times": self.times,
            "grades": self.grades,
            "parent": self.parent,
            "baseline": self.baseline,
            "baseline_grades": self.baseline_grades,
            "relative_grades": self.relative_grades}
        torch.save(checkpoint, path)

    def clone(self):
        """
        Create a clone of `self` and return it. The clone's `parent` and `baseline` attributes (if present) will be the same reference as the original. Everything else will be a deep copy.
        """
        tmp1 = self.parent
        tmp2 = self.baseline
        self.parent = None
        self.baseline = None
        clone = copy.deepcopy(self)
        self.parent = tmp1
        clone.parent = tmp1
        self.baseline = tmp2
        clone.baseline = tmp2
        return clone

    def push(self):
        """
        Remove the current `self.parent` reference from `self`.
        Create a clone of `self` and store it in `self.parent`.
        Set the baseline model to the new parent's model.
        """
        self.parent = None  # until we figure out the memory situation of actually making a stack of these
        self.parent = self.clone()
        self.baseline = self.parent.model

    def pop(self):
        """
        Revert to the state stored in `self.parent` on the previous `backup` call.
        If no such call took place, then do nothing.
        """
        if self.parent is None:
            return
        clone = self.parent.clone()
        self.model = clone.model
        self.optimizer = clone.optimizer
        self.dataset = clone.dataset
        self.batch_size = clone.batch_size
        self.example_length = clone.example_length
        self.time = clone.time
        self.times.clear()
        self.times.extend(clone.times)
        self.grades.clear()
        self.grades.extend(clone.grades)
        self.parent = clone.parent
        self.baseline = clone.baseline
        self.baseline_grades.clear()
        self.baseline_grades.extend(clone.baseline_grades)
        self.relative_grades.clear()
        self.relative_grades.extend(clone.relative_grades)

    @autocast()
    def study(self):
        """
        Use `self.optimizer` to train `self.model` for one step using a batch obtained from `self.dataset` using training hyperparameters `self.batch_size` and `self.example_length`.
        Add/append the resulting training data to `self.time`, `self.times`, `self.grades`, `self.baseline_grades`, and `self.relative_grades`.
        """
        def closure():
            batch = self.dataset.batch(batch_size=self.batch_size, example_length=self.example_length)
            losses = self.model(batch)
            losses = torch.nan_to_num(losses, nan=0.0, posinf=0.0, neginf=0.0)
            torch.mean(losses).backward()
            losses = losses.detach().cpu().numpy()
            if self.baseline is not None:
                baseline_losses = self.baseline(batch).detach().cpu().numpy()
            else:
                baseline_losses = None
            return losses, baseline_losses
        start = time()
        losses, baseline_losses = self.optimizer.step(closure)
        elapsed = time() - start
        self.time += elapsed
        self.times.append(elapsed)
        grade = 1.0 - np.mean(losses)
        self.grades.append(grade)
        if self.baseline is not None:
            baseline_grade = 1.0 - np.mean(baseline_losses)
            relative_grade = grade/(1e-8+baseline_grade)
        else:
            baseline_grade = grade
            relative_grade = 1.0
        self.baseline_grades.append(baseline_grade)
        self.relative_grades.append(relative_grade)

    def parameter_histograms(self):
        """
        Return a dictionary the keys of which are the names of parameters
        as returned by `self.model.named_parameters()` and the values of
        which are pairs (X, Y) which give the pdf of the distribution of
        individual parameter values.
        ### Example
        ```python
        H = student.parameter_histograms()
        plots = [Plot(x="value",y=f"pdf",**{key: H[key]}) for key in H]
        plots[0]
        ```
        """
        pd = {name: p for (name, p) in self.model.named_parameters()}
        H = {}
        for (name, p) in pd.items():
            n = torch.numel(p)
            bins = math.floor(math.sqrt(n))
            data = p.detach().cpu().numpy().reshape(-1)
            Y, X = np.histogram(data, bins=int(len(data)**(1/2)), density=True)
            H[name] = (X, Y)
        return H

    def mutate(self):
        """
        Mutate `self` by randomly altering `self.batch_size` and `self.optimizer.param_groups[0]["lr"]`
        """
        r = random.choice([0.5, 0.75, 1.0/0.75, 2.0])
        self.batch_size = int(r*self.batch_size)
        r = random.choice([0.5, 0.75, 1.0/0.75, 2.0])
        lr = self.optimizer.param_groups[0]["lr"](0)
        lr = lr*r
        if lr == 0.0:
            lr = 1e-6  # minimum learning rate, maybe should lower
        self.optimizer.param_groups[0]["lr"] = lambda n: lr

    @torch.no_grad()
    def autocomplete(self, prompt=None, n_generate=128, n_ctx=None, encode=None, decode=None, output=None):
        """
        Autocomplete using the model

        ## Args
        * `prompt: str` an optional prompt to begin with
        * `n_generate: int` the number of bytes/tokens to generate
        * `n_ctx: int` the number of bytes/tokens in the context window
        * `encode: TODO` the function that can turn an str into a sequence of bytes/tokens suitable for the model.
        defaults to utf8encode
        * `decode: TODO` the function that can turn the sequences of bytes/tokens used by the model to a str
        defaults to utf8decode
        * `output: Optional[List[int]]` a list to stream the output bytes/tokens to (as `int`s; they will not be decoded to `str`).

        ## TODO
        * make streaming autocomplete with streamed characters (i.e. length 1 strings) using asyncio
        """
        Categorical = torch.distributions.Categorical
        if n_ctx is None:
            n_ctx = self.model.n_ctx
        if encode is None:
            encode = utf8encode
        if decode is None:
            decode = utf8decode
        if prompt is None:
            prompt = decode(self.dataset.batch(1, 2*n_ctx).tolist()[0])  # kludge
        x = encode(prompt)
        x = x[-n_ctx:]
        def sampler(x):
            x = list(x)
            for _ in range(n_generate):
                y = Categorical(self.model.inference(torch.tensor(x,dtype=torch.long,device='cuda').unsqueeze(0)).view(-1)[-self.model.n_vocab_out:]).sample().item()
                x = (x + [y])[-n_ctx:]
                if output is not None:
                    output.append(y)
                yield y
        return decode(list(sampler(x)))
