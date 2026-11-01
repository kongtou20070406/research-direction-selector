"""Score the frozen iris comparison. The criterion hash is checked first."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parent
CRITERION = ROOT / "iris-criterion.md"
EXPECTED = (ROOT / "iris-criterion.sha256").read_text(encoding="utf-8").strip()
ACTUAL = hashlib.sha256(CRITERION.read_bytes()).hexdigest()
if ACTUAL != EXPECTED:
    sys.exit("criterion hash mismatch: " + ACTUAL)


def split():
    data = load_iris()
    order = np.random.Generator(np.random.PCG64(0)).permutation(150)
    values, labels = data.data[order], data.target[order]
    return (values[:105], labels[:105]), (values[105:127], labels[105:127]), (values[127:], labels[127:])


def scale(train, other, columns):
    mean = train[:, columns].mean(axis=0)
    std = train[:, columns].std(axis=0)
    std = np.where(std == 0, 1.0, std)
    return (train[:, columns] - mean) / std, (other[:, columns] - mean) / std


def fit_count(train, dev, columns):
    train_x, dev_x = scale(train[0], dev[0], columns)
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, tol=1e-4, random_state=0)
    model.fit(train_x, train[1])
    return int(np.sum(model.predict(dev_x) == dev[1]))


def majority(train, dev):
    counts = np.bincount(train[1], minlength=3)
    label = int(np.argmax(counts))
    return int(np.sum(dev[1] == label))


def verdict(left, right, left_name, right_name):
    gap = left - right
    if abs(gap) < 2:
        return "INDISTINGUISHABLE"
    return left_name if gap > 0 else right_name


def main():
    train, dev, test = split()
    dev_sepal = fit_count(train, dev, [0, 1])
    dev_all = fit_count(train, dev, [0, 1, 2, 3])
    dev_majority = majority(train, dev)
    decision = verdict(dev_all, dev_sepal, "ALL_FEATURES", "SEPAL")
    test_sepal = fit_count(train, test, [0, 1])
    test_all = fit_count(train, test, [0, 1, 2, 3])
    out = {
        "criterion_sha256": ACTUAL,
        "model_identity": "none",
        "token_usage": None,
        "dev": {"n": 22, "majority": dev_majority, "sepal": dev_sepal, "all": dev_all,
               "verdict": decision,
               "sepal_vs_majority": verdict(dev_sepal, dev_majority, "SEPAL", "MAJORITY"),
               "all_vs_majority": verdict(dev_all, dev_majority, "ALL_FEATURES", "MAJORITY")},
        "test_once": {"n": 23, "sepal": test_sepal, "all": test_all,
                      "agrees_with_dev": (test_all - test_sepal) * (dev_all - dev_sepal) >= 0},
        "scientific_support": "UNKNOWN",
    }
    Path(sys.argv[1]).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
