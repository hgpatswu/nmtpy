# -*- coding: utf-8 -*-
import os
import sys
import copy
import gzip
import tempfile
import subprocess

from . import cleanup

def print_summary(train_args, model_args, print_func=None):
    """Returns or prints a summary of training/model options."""
    def _get_max_width(keys):
        return max([len(k) for k in keys]) + 1

    def _dict_str(d, maxlen):
        res = ""
        templ = '%' + str(maxlen) + 's : '
        kvs = []
        for k,v in d.items():
            if isinstance(v, list):
                kvs.append((k, v.pop(0)))
                for l in v:
                    kvs.append((k, l))
            else:
                kvs.append((k,v))

        kvs = sorted(kvs, key=lambda x: x[0])
        for k,v in kvs:
            res += (templ % k) + str(v) + '\n'
        return res

    train_args = copy.deepcopy(train_args)
    model_args = copy.deepcopy(model_args)
    max_width = _get_max_width(train_args.__dict__.keys() +
                               model_args.__dict__.keys())

    # Add training options
    result  = 'Training options:'
    result += '\n' + ('-' * 35) + '\n'

    result += _dict_str(train_args.__dict__, max_width)

    # Copy
    model_args = dict(model_args.__dict__)
    # Remove these and treat them separately
    model_data = model_args.pop('data')
    model_dict = model_args.pop('dicts')

    # Add model options
    result += '\nModel options:'
    result += '\n' + ('-' * 35) + '\n'

    result += _dict_str(model_args, max_width)
    result += ('%' + str(max_width) + 's =\n') % 'dicts'
    result += _dict_str(model_dict, max_width)
    result += ('%' + str(max_width) + 's =\n') % 'data'
    result += _dict_str(model_data, max_width)

    if print_func:
        for line in result.split('\n'):
            print_func(line)
    else:
        return result

def pretty_dict(elem, msg=None, print_func=None):
    """Returns a string representing elem optionally prepended by a message."""
    result = ""
    if msg:
        # Add message
        result += msg + '\n'
        # Add trailing lines
        result += ('-' * len(msg)) + '\n'

    skeys = sorted(elem.keys())
    maxlen = max([len(k) for k in skeys]) + 1
    templ = '%' + str(maxlen) + 's : '
    for k in skeys:
        result += (templ % k) + str(elem[k]) + '\n'

    if print_func:
        for line in result.split('\n'):
            print_func(line)
    else:
        return result

def ensure_dirs(dirs):
    """Create a list of directories if not exists."""
    try:
        for d in dirs:
            os.makedirs(d)
    except OSError as oe:
        pass

def real_path(p):
    """Expand UNIX tilde and return real path."""
    return os.path.realpath(os.path.expanduser(p))

def listify(l):
    """Encapsulate l with list[] if not."""
    return [l] if not isinstance(l, list) else l

def readable_size(n):
    """Return a readable size string."""
    sizes = ['K', 'M', 'G']
    fmt = ''
    size = n
    for i,s in enumerate(sizes):
        nn = n / (1000.**(i+1))
        if nn >= 1:
            size = nn
            fmt = sizes[i]
        else:
            break
    return '%.1f%s' % (size, fmt)

def get_temp_file(suffix="", name=None, delete=False):
    """Creates a temporary file under /tmp."""
    if name:
        name = os.path.join("/tmp", name)
        t = open(name, "w")
        cleanup.register_tmp_file(name)
    else:
        _suffix = "_nmtpy_%d" % os.getpid()
        if suffix != "":
            _suffix += suffix

        t = tempfile.NamedTemporaryFile(suffix=_suffix, delete=delete)
        cleanup.register_tmp_file(t.name)
    return t

def get_valid_evaluation(save_path, beam_size, n_jobs, metric, mode, valid_mode='single'):
    """Run nmt-translate for validation during training."""
    cmd = ["nmt-translate", "-b", str(beam_size), "-D", mode,
           "-j", str(n_jobs), "-m", save_path, "-M", metric, "-v", valid_mode]

    # nmt-translate will print a dict of metrics
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=sys.stdout)
    cleanup.register_proc(p.pid)
    out, err = p.communicate()
    cleanup.unregister_proc(p.pid)
    results = eval(out.splitlines()[-1].strip())
    return results[metric]

def create_gpu_lock(used_gpu):
    """Create a lock file for GPU reservation."""
    name = "gpu_lock.pid%d.gpu%s" % (os.getpid(), used_gpu)
    lockfile = get_temp_file(name=name)
    lockfile.write("[nmtpy] %s\n" % name)

def fopen(filename, mode='r'):
    """GZIP-aware file opening function."""
    if filename.endswith('.gz'):
        return gzip.open(filename, mode)
    return open(filename, mode)

def find_executable(fname):
    """Find executable in PATH."""
    fname = os.path.expanduser(fname)
    if os.path.isabs(fname) and os.access(fname, os.X_OK):
        return fname
    for path in os.environ['PATH'].split(':'):
        fpath = os.path.join(path, fname)
        if os.access(fpath, os.X_OK):
            return fpath

def get_device(which='auto'):
    """Return Theano device to use by favoring GPUs first."""
    # Use CPU
    if which == "cpu":
        return "cpu", None

    # Use the requested GPU without looking for availability
    elif which.startswith("gpu"):
        create_gpu_lock(int(which.replace("gpu", "")))
        return which

    # auto favors GPU in the first place
    elif which == 'auto':
        try:
            out = subprocess.check_output(["nvidia-smi", "-q"])
        except OSError as oe:
            # Binary not found, fallback to CPU
            return "cpu"

        # Find out about GPU usage
        usage = ["None" in l for l in out.split("\n") if "Processes" in l]

        try:
            # Get first unused one
            which = usage.index(True)
        except ValueError as ve:
            # No available GPU on this machine
            return "cpu"

        lock_file = create_gpu_lock(which)
        return ("gpu%d" % which)

def get_exp_identifier(train_args, model_args, suffix=None):
    """Return a representative string for the experiment."""

    names = [train_args.model_type]

    for k in sorted(model_args.__dict__):
        if k.endswith("_dim"):
            # Only the first letter should suffice for now, e for emb, r for rnn
            names.append('%s%d' % (k[0], getattr(model_args, k)))

    # Join so far
    name = '-'.join(names)

    # Append optimizer and learning rate
    name += '-%s_%.e' % (model_args.optimizer, model_args.lrate)

    # Append batch size
    name += '-bs%d' % model_args.batch_size

    # Validation stuff
    name += '-%s' % train_args.valid_metric

    if train_args.valid_freq > 0:
        name += "-each%d" % train_args.valid_freq
    else:
        name += "-eachepoch"

    if train_args.decay_c > 0:
        name += "-l2_%.e" % train_args.decay_c

    if 'emb_dropout' in model_args:
        name += "-do_%.1f_%.1f_%.1f" % (model_args.emb_dropout,
                                        model_args.ctx_dropout,
                                        model_args.out_dropout)

    if train_args.clip_c > 0:
        name += "-gc%d" % int(train_args.clip_c)

    if train_args.alpha_c > 0:
        name += "-alpha_%.e" % train_args.alpha_c

    if isinstance(model_args.weight_init, str):
        name += "-init_%s" % model_args.weight_init
    else:
        name += "-init_%.2f" % model_args.weight_init

    # Append seed
    name += "-s%d" % train_args.seed

    if suffix:
        name = "%s-%s" % (name, suffix)

    return name

def get_next_runid(save_path, exp_id):
    # Log file, runs start from 1, incremented if exists
    i = 1
    while os.path.exists(os.path.join(save_path, "%s.%d.log" % (exp_id, i))):
        i += 1

    return i
