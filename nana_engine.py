"""NANA's brain: loads the trained models and decides exactly the way the Colab v4 notebook does.

The models folder (default ./models) must contain the files from Google Drive `MyDrive/NANA/models_v4_FINAL`:
    classic.joblib, nana_config.json, nana_text.py, transformer_seed0/ (optional)
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np

HAM, SPAM, SCAM, UNSURE, NOTICE = 0, 1, 2, 3, 4


@dataclass
class Verdict:
    code: int                     # HAM, SPAM, SCAM or UNSURE
    scam: float                   # model probabilities
    junk: float
    normal: float
    reasons: list = field(default_factory=list)
    family_check: bool = False    # "call them on the number you already have"
    good: list = field(default_factory=list)       # reasons it looks fine (only ones that are true)
    steps: str = ""                                # what to do, in plain words


class NanaEngine:
    def __init__(self, model_dir="models", classic_only=False):
        self.model_dir = os.path.abspath(model_dir)
        cfg_path = os.path.join(self.model_dir, "nana_config.json")
        if not os.path.exists(cfg_path):
            raise FileNotFoundError(f"Can't find {cfg_path}. Copy the files from MyDrive/NANA/models_v4_FINAL into this folder.")
        with open(cfg_path) as f:
            self.config = json.load(f)

        # nana_text.py must be importable: the saved classic model refers to its functions
        if self.model_dir not in sys.path:
            sys.path.insert(0, self.model_dir)
        import nana_text
        self.text = nana_text

        import joblib
        self.classic = joblib.load(os.path.join(self.model_dir, "classic.joblib"))
        self.warnings = []
        try:
            import sklearn
            if sklearn.__version__ != self.config.get("sklearn_version"):
                self.warnings.append(f"scikit-learn {sklearn.__version__} is installed but the model was saved with "
                                     f"{self.config.get('sklearn_version')}. If results look odd, install that version.")
        except Exception:
            pass

        self.tf_models, self.tokenizer = [], None
        tf_dir = os.path.join(self.model_dir, "transformer_seed0")
        want_tf = (not classic_only) and self.config.get("transformer_name") and os.path.isdir(tf_dir)
        if want_tf:
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
                self.torch = torch
                self.tokenizer = AutoTokenizer.from_pretrained(tf_dir)
                for s in range(int(self.config.get("n_seeds", 1))):
                    d = os.path.join(self.model_dir, f"transformer_seed{s}")
                    if os.path.isdir(d):
                        self.tf_models.append(AutoModelForSequenceClassification.from_pretrained(d).eval())
            except Exception as e:                       # fall back to the classic model
                self.warnings.append(f"Couldn't load the transformer ({e.__class__.__name__}); using the classic model only.")
                self.tf_models = []

        # pick the model + thresholds exactly as tested in the notebook
        th = self.config["thresholds"]
        if self.tf_models and self.config.get("ensemble_classic_weight") is not None and self.config["nana_model"].startswith("ensemble"):
            self.mode, self.weight = "ensemble", float(self.config["ensemble_classic_weight"])
            key = self.config["nana_model"]
        elif self.tf_models and self.config["nana_model"].startswith("transformer"):
            self.mode, self.weight, key = "transformer", 0.0, self.config["nana_model"]
        else:
            self.mode, self.weight = "classic", 1.0
            key = next(k for k in th if k.startswith("classic"))
        self.model_name = key
        self.t_warn, self.t_unsure = th[key]["warn"], th[key]["unsure"]
        self.use_flags = bool(self.config.get("use_red_flags", True))

        # word-level explanations come from the logistic-regression part of the classic model
        self._lr = None
        explainer = os.path.join(self.model_dir, "classic_logreg_explainer.joblib")
        lr = joblib.load(explainer) if os.path.exists(explainer) else self.classic
        try:
            self._words = lr.named_steps["features"].transformer_list[0][1]
            self._coef = lr.named_steps["clf"].coef_[SCAM]
            self._names = self._words.get_feature_names_out()
            self._lr = lr
        except Exception:
            pass

    # ------------------------------------------------------------------ probabilities
    def _tf_proba(self, texts):
        texts = [self.text.transformer_prep(t) for t in texts]
        total = 0
        for model in self.tf_models:
            enc = self.tokenizer(texts, truncation=True, max_length=int(self.config.get("max_len", 128)),
                                 padding=True, return_tensors="pt")
            with self.torch.no_grad():
                total = total + self.torch.softmax(model(**enc).logits.float(), dim=-1).numpy()
        return total / len(self.tf_models)

    def proba(self, texts):
        texts = list(texts)
        pc = self.classic.predict_proba(texts) if self.mode != "transformer" else 0
        if self.mode == "classic":
            return np.asarray(pc)
        pt = self._tf_proba(texts)
        return pt if self.mode == "transformer" else self.weight * pc + (1 - self.weight) * pt

    # ------------------------------------------------------------------ decision (same as notebook step 5)
    def _decide(self, p, text):
        warn = 1.0 - p[HAM]
        kind = SCAM if p[SCAM] >= p[SPAM] else SPAM
        out = kind if warn >= self.t_warn else (UNSURE if warn >= self.t_unsure else HAM)
        flags = self.text.red_flags(text)
        if self.use_flags and any(strong for _, strong, _ in flags) and out != SPAM:
            out = SCAM
        return out, flags

    def scam_words(self, text, k=3):
        if self._lr is None:
            return []
        v = self._words.transform([text])
        idx = v.nonzero()[1]
        if not len(idx):
            return []
        w = self._coef[idx] * v.data
        ranked = sorted(zip(self._names[idx], w), key=lambda x: -x[1])
        return [n for n, s in ranked[:k] if s > 0 and n not in ("url", "number", "email")]

    # ------------------------------------------------------------------ the full check used by the app
    FAMILY = re.compile(r"\b(mom|mum|mommy|mother|dad|daddy|father|grandma|grandpa|granny|gran|nana|papa|grammy|"
                        r"grandson|granddaughter|son|daughter|it'?s me|your (son|daughter|grandson|granddaughter|nephew|niece))\b", re.I)
    MONEY = re.compile(r"(\$\s?\d|\bmoney\b|\bpay\b|\bsend\b|\bzelle\b|\bvenmo\b|\bcash ?app\b|\btransfer\b|\bwire\b|"
                       r"\bgift ?cards?\b|\bbail\b|\bloan\b|\bbill\b|\brent\b|\bbank\b)", re.I)

    def check(self, message, sender="unknown"):
        """sender: 'known' (saved contact), 'unknown' (number I don't recognize) or 'not_sure'."""
        import lantern_rules as rules
        p = self.proba([message])[0]
        warn = 1.0 - p[HAM]
        kind = SCAM if p[SCAM] >= p[SPAM] else SPAM
        code = kind if warn >= self.t_warn else (UNSURE if warn >= self.t_unsure else HAM)
        r = rules.review(message)
        reasons = [why for name, _, why in r["found"]]
        family_check, good, steps = False, [], ""

        claims_family = bool(self.FAMILY.search(message))
        asks_money = bool(self.MONEY.search(message))
        if r["strong"]:
            code = SCAM                                     # it asks for something no honest sender asks for
        elif r["all_links_official"] and not r["asks"] and not claims_family:
            # Claims to be a company, every link goes to that company's real website, and it asks for nothing.
            # Real security alerts look exactly like this, and so do copies of them, so: probably real, check safely.
            code = NOTICE
            reasons = [x for name, _, x in r["found"] if name in ("pressure", "threat")]
            good = rules.good_signs(r)
            brand = r["claimed"][0]
            steps = rules.safe_steps(brand, rules.BRANDS[brand][1][0], message)

        if sender != "known" and claims_family and asks_money:
            # the classic "grandparent" scam: someone claiming to be family, from a number you don't know, asking for money
            code, family_check = SCAM, True
            reasons = [x for x in reasons if "new phone number" not in x]
            reasons.insert(0, "Someone says they're family, from a number you don't have saved, and asks for money.")
        elif sender != "known" and claims_family and code == HAM:
            code, family_check = UNSURE, True              # a "new number" text with no money request yet: double-check
            reasons.insert(0, "Someone says they're family, but from a number you don't have saved.")
        elif sender == "known" and code in (SCAM, UNSURE) and not r["strong"]:
            reasons.append("It's from a saved contact, but their phone or account could have been hacked.")

        if code in (SCAM, UNSURE) and not reasons:
            reasons = ["It's written the way many scam messages are, but I couldn't point to one clear warning sign. Check with someone you trust."
                       if code == UNSURE else "It's written the way many scam messages I learned from are written."]
        if code == HAM:
            reasons, good = [], rules.good_signs(r)         # only reasons it looks fine, never scary ones
        if code == SPAM:
            reasons = [x for name, _, x in r["found"] if name in ("prize", "pressure", "short_link")]
        return Verdict(code, float(p[SCAM]), float(p[SPAM]), float(p[HAM]), reasons, family_check, good, steps)
