"""Opt-in, fail-closed Linux benchmark worker isolation, separate from ProjectStore.

The evaluator is trusted. It explicitly projects public scalar fields and pins
worker, public files and a clean Python runtime inventory by SHA-256. This module
does not export RDS bindings, discover credentials, or enable model networking.
"""
import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from rds_bounded_io import read_regular_bytes

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_RUNTIME_BYTES = 128 * 1024 * 1024
MAX_RUNTIME_FILES = 5000
MAX_PUBLIC_FILES = 64
RESPONSE_KEYS = {'status', 'source', 'policy_json', 'reason'}
RESPONSE_STATUSES = {'proposed', 'no_feasible_method', 'needs_authorization'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


def _scalar(value):
    return value is None or type(value) in (str, bool, int) or (type(value) is float and math.isfinite(value))


def project_request(request, fields):
    """Select named scalar leaves, never implicitly forward an entire RDS request.

    Example: {'objective': ('task', 'objective'), 'base_source': ('base_source',)}.
    Nested policy/evaluator objects require a separately reviewed public export;
    passing their parent field is rejected instead of recursively copying it.
    """
    if not isinstance(request, dict) or not isinstance(fields, dict) or not fields:
        raise ValueError('An explicit nonempty public field projection is required')
    projected = {}
    for name, path in fields.items():
        if not isinstance(name, str) or not name or not isinstance(path, (tuple, list)) or not path:
            raise ValueError('Invalid public field projection')
        value = request
        for key in path:
            if isinstance(value, dict) and isinstance(key, str) and key in value:
                value = value[key]
            elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
                value = value[key]
            else:
                raise ValueError('Missing public field: ' + name)
        if not _scalar(value):
            raise ValueError('Public projection must select scalar leaves: ' + name)
        projected[name] = value
    _request_bytes(projected)
    return projected


def _write_record(path, raw):
    """Publish a complete record durably before dispatch or reporting completion."""
    temporary = path.with_name('.' + path.name + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if sys.platform == 'linux':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _record(path, value):
    _write_record(path, (canonical(value) + '\n').encode('utf-8'))


def _request_bytes(request):
    if not isinstance(request, dict) or not request or not all(
            isinstance(k, str) and k and _scalar(v) for k, v in request.items()):
        raise ValueError('Public request must be a nonempty object of explicitly selected scalar fields')
    raw = canonical(request).encode('utf-8') + b'\n'
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Public request exceeds byte bound')
    return raw


def _destination(value, *, runtime=False):
    if not isinstance(value, str) or '\\' in value or '\x00' in value or value.startswith('//'):
        raise ValueError('Invalid staged path')
    path = PurePosixPath(value)
    if '..' in path.parts or str(path) != value or path.is_absolute() != runtime:
        raise ValueError('Staged paths must be canonical and have the required root')
    if not path.parts or str(path) in ('.', '/') or (runtime and len(path.parts) < 2):
        raise ValueError('Empty staged path')
    if runtime and path.parts[1] not in ('usr', 'lib', 'lib64', 'runtime'):
        raise ValueError('Runtime destinations must be below /usr, /lib, /lib64 or /runtime')
    return path


def _read_pinned(spec, private_roots, byte_limit):
    if not isinstance(spec, dict) or set(spec) != {'source', 'path', 'sha256'}:
        raise ValueError('File inventory requires source, path and sha256')
    source = Path(spec['source'])
    if not source.is_absolute():
        raise ValueError('Source paths must be absolute')
    source = source.resolve(strict=True)
    if source.parts[1:2] in (('proc',), ('sys',), ('dev',)):
        raise ValueError('Kernel, device and descriptor paths cannot be exported')
    if any(source.is_relative_to(root) for root in private_roots):
        raise ValueError('A declared private file cannot be exported')
    expected = spec['sha256']
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Files require a lowercase SHA-256 identity')
    # Walk canonical components with openat/O_NOFOLLOW, not a directory bind.
    # The SHA check also prevents a changed source from entering the snapshot.
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in source.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(child)
            if not stat.S_ISREG(info.st_mode) or info.st_size > byte_limit:
                raise ValueError('Only bounded regular files can be exported')
            with os.fdopen(child, 'rb', closefd=False) as stream:
                raw = stream.read(byte_limit + 1)
        finally:
            os.close(child)
    finally:
        os.close(fd)
    if len(raw) > byte_limit or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Exported file identity or byte bound changed')
    return raw


def _stage_file(root, path, raw, *, executable=False):
    target = root / str(path).lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(raw)
    target.chmod(0o555 if executable else 0o444)


# Run before exec rather than using preexec_fn (unsafe in threaded callers).
_LIMIT_LAUNCHER = '''import os,resource,sys
wall,memory=int(sys.argv[1]),int(sys.argv[2])
for key,value in [(resource.RLIMIT_CPU,wall),(resource.RLIMIT_AS,memory),
                  (resource.RLIMIT_NPROC,64),(resource.RLIMIT_NOFILE,64),
                  (resource.RLIMIT_FSIZE,1048576),(resource.RLIMIT_CORE,0)]:
    resource.setrlimit(key,(value,value))
os.execv(sys.argv[3],sys.argv[3:])
'''


def _sealed_fd(name, raw):
    import fcntl
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        with os.fdopen(os.dup(fd), 'wb') as stream:
            stream.write(raw)
        # Linux UAPI: F_ADD_SEALS=1033, F_GET_SEALS=1034; some Python
        # builds omit the names. Verify the kernel accepted every required seal.
        fcntl.fcntl(fd, 1033, 0x0F)  # SEAL_SEAL | SHRINK | GROW | WRITE
        if fcntl.fcntl(fd, 1034) & 0x0F != 0x0F:
            raise ValueError('ISOLATION_UNAVAILABLE: immutable memfd sealing failed')
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _seccomp_program():
    # Linux UAPI audit.h, asm-generic/unistd.h and x86 syscall_64.tbl.
    # Reject compat architectures and x32 rather than interpreting their numbers.
    architectures = {
        'x86_64': (0xC000003E, {'socket': 41, 'socketpair': 53, 'add_key': 248,
                              'request_key': 249, 'keyctl': 250, 'ptrace': 101,
                              'process_vm_readv': 310, 'process_vm_writev': 311}),
        'aarch64': (0xC00000B7, {'socket': 198, 'socketpair': 199, 'add_key': 217,
                              'request_key': 218, 'keyctl': 219, 'ptrace': 117,
                              'process_vm_readv': 270, 'process_vm_writev': 271}),
    }
    if sys.byteorder != 'little' or os.uname().machine not in architectures:
        raise ValueError('ISOLATION_UNAVAILABLE: unsupported seccomp architecture')
    arch, calls = architectures[os.uname().machine]
    calls = dict(calls, io_uring_setup=425, io_uring_enter=426, io_uring_register=427, pidfd_getfd=438)
    # sock_filter: code, jt, jf, k. seccomp_data has nr at 0, arch at 4.
    instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
                    (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000)]
    for number in calls.values():
        instructions += [(0x15, 0, 1, number), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    instructions.append((0x06, 0, 0, 0x7FFF0000))
    return b''.join(struct.pack('=HBBI', *instruction) for instruction in instructions), calls


def _command(bwrap, runtime_root, public_root, code_root, python, program, seccomp_fd):
    # No host root, home, cwd, /etc, sockets or writable host filesystem enters.
    return [bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
            '--unshare-uts', '--hostname', 'rds-blind', '--disable-userns', '--assert-userns-disabled',
            '--die-with-parent', '--new-session', '--cap-drop', 'ALL', '--clearenv',
            '--seccomp', str(seccomp_fd),
            '--ro-bind', str(runtime_root), '/', '--proc', '/proc',
            '--ro-bind', str(Path(code_root) / 'empty'), '/proc/keys',
            '--ro-bind', str(Path(code_root) / 'empty'), '/proc/key-users',
            '--ro-bind', str(Path(code_root) / 'empty-proc-one'), '/proc/1',
            '--dev', '/dev', '--remount-ro', '/dev',
            '--ro-bind', str(public_root), '/public', '--ro-bind', str(code_root), '/rds',
            '--size', str(32 * 1024 * 1024), '--tmpfs', '/work', '--chdir', '/work',
            '--setenv', 'HOME', '/nonexistent', '--setenv', 'TMPDIR', '/work',
            '--setenv', 'PATH', '/nonexistent', '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'PWD', '/work',
            '--', python, '-I', '-S', '-B', program]


def _capture(argv, stdin_path, output_dir, prefix, timeout, max_bytes, memory_bytes, env=None, seccomp_fd=None):
    """Retain bounded original stream prefixes, kill on overflow or wall expiry."""
    started = time.monotonic()
    outcome = {'returncode': None, 'timed_out': False, 'output_limit': False,
               'stdout_bytes': 0, 'stderr_bytes': 0, 'error': None}
    command = [sys.executable, '-I', '-S', '-c', _LIMIT_LAUNCHER,
               str(max(1, math.ceil(timeout))), str(memory_bytes), *argv]
    process = None
    with (output_dir / (prefix + 'stdout.bin')).open('xb') as stdout, \
            (output_dir / (prefix + 'stderr.bin')).open('xb') as stderr, \
            os.fdopen(_sealed_fd('rds-blind-input', stdin_path.read_bytes()), 'rb') as source:
        try:
            if seccomp_fd is not None:
                os.lseek(seccomp_fd, 0, os.SEEK_SET)
            process = subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=output_dir, env=env or {'LANG': 'C.UTF-8'},
                                       close_fds=True, pass_fds=(() if seccomp_fd is None else (seccomp_fd,)),
                                       start_new_session=True, shell=False)
            with selectors.DefaultSelector() as selector:
                for pipe, name, retained in ((process.stdout, 'stdout', stdout), (process.stderr, 'stderr', stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, (name, retained))
                killed = False
                while selector.get_map() or process.poll() is None:
                    if time.monotonic() - started >= timeout and not killed:
                        outcome['timed_out'] = True
                        killed = True
                        _kill(process)
                    for key, _ in selector.select(.02):
                        raw = os.read(key.fd, 65536)
                        if not raw:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        name, retained = key.data
                        old = outcome[name + '_bytes']
                        outcome[name + '_bytes'] += len(raw)
                        retained.write(raw[:max(0, max_bytes - old)])
                        if outcome[name + '_bytes'] > max_bytes and not killed:
                            outcome['output_limit'] = True
                            killed = True
                            _kill(process)
                    # A broken launcher must not keep collection open forever.
                    if killed and time.monotonic() - started > timeout + 3:
                        raise TimeoutError('Process termination or pipe closure was not established')
                outcome['returncode'] = process.wait(timeout=2)
        except (OSError, ValueError, subprocess.SubprocessError, TimeoutError) as exc:
            outcome['error'] = str(exc)
        finally:
            if process is not None:
                if process.poll() is None:
                    _kill(process)
                try:
                    outcome['returncode'] = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    outcome['error'] = 'Process termination was not established'
                for pipe in (process.stdout, process.stderr):
                    if pipe and not pipe.closed:
                        pipe.close()
    outcome['wall_seconds'] = time.monotonic() - started
    return outcome


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


_PROBE = r'''import ctypes,errno,fcntl,json,os,pathlib,socket,sys
request=json.load(sys.stdin)
checks={}
checks['anonymous_stdin']=os.readlink('/proc/self/fd/0').startswith('/memfd:rds-blind-input')
checks['sealed_stdin']=fcntl.fcntl(0,1034)&0x0F==0x0F
checks['reaper_proc_hidden']=(os.path.samestat(os.stat('/proc/1'),os.stat('/rds/empty-proc-one'))
    and bool(os.statvfs('/proc/1').f_flag & os.ST_RDONLY)
    and not list(pathlib.Path('/proc/1').iterdir()))
checks['key_metadata_hidden']=(pathlib.Path('/rds/empty').read_bytes()==b'' and all(
    os.path.samestat(os.stat(path),os.stat('/rds/empty'))
    and os.statvfs(path).f_flag & os.ST_RDONLY for path in ('/proc/keys','/proc/key-users')))
try: os.fstat(request['seccomp_fd'])
except OSError: checks['seccomp_fd_closed']=True
else: checks['seccomp_fd_closed']=False
for label,path in request['hidden'].items():
    try:
        with open(path,'rb') as stream: stream.read(1)
    except (OSError,ValueError): checks[label]=True
    else: checks[label]=False
checks['environment']='RDS_HOST_SECRET_CANARY' not in os.environ
try: os.fstat(request['canary_fd'])
except OSError: checks['inherited_fd']=True
else: checks['inherited_fd']=False
checks['pid_namespace']=os.readlink('/proc/self/ns/pid')!=request['pid_namespace']
checks['net_namespace']=os.readlink('/proc/self/ns/net')!=request['net_namespace']
checks['user_namespace']=os.readlink('/proc/self/ns/user')!=request['user_namespace']
checks['public_input']=json.loads(pathlib.Path('/public/request.json').read_text())==request['public_request']
for label,path in [('public_readonly','/public/request.json'),('runtime_readonly',sys.executable),
                   ('code_readonly','/rds/probe.py')]:
    try: fd=os.open(path,os.O_WRONLY|os.O_APPEND)
    except OSError: checks[label]=bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    else: os.close(fd); checks[label]=False
for label,path in [('device_files_readonly','/dev/rds-write-canary'),
                   ('shared_memory_readonly','/dev/shm/rds-write-canary')]:
    try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except OSError: checks[label]=bool(os.statvfs(os.path.dirname(path)).f_flag & os.ST_RDONLY)
    else: os.close(fd); checks[label]=False
try: sock=socket.socket()
except OSError as exc:
    checks['sockets_denied']=exc.errno==errno.EPERM
    checks['host_network']=checks['sockets_denied']
else:
    checks['sockets_denied']=False
    sock.settimeout(.3)
    try: sock.connect(('127.0.0.1',request['port']))
    except OSError: checks['host_network']=True
    else: checks['host_network']=False
    finally: sock.close()
try: pair=socket.socketpair()
except OSError as exc: checks['socketpair_denied']=exc.errno==errno.EPERM
else:
    for sock in pair: sock.close()
    checks['socketpair_denied']=False
libc=ctypes.CDLL(None,use_errno=True)
# Invalid arguments only: never inspect, create or search actual host keys.
checks['keyring_syscalls_denied']=True
checks['io_uring_syscalls_denied']=True
checks['process_introspection_denied']=True
for name,number in request['blocked_syscalls'].items():
    if name in ('socket','socketpair'): continue
    ctypes.set_errno(0)
    result=libc.syscall(ctypes.c_long(number),ctypes.c_long(-1 if name in
        ('keyctl','io_uring_enter','io_uring_register','ptrace','process_vm_readv',
         'process_vm_writev','pidfd_getfd') else 0),
        ctypes.c_long(0),ctypes.c_long(0),ctypes.c_long(0),ctypes.c_long(0),ctypes.c_long(0))
    group=('io_uring_syscalls_denied' if name.startswith('io_uring') else
           'keyring_syscalls_denied' if name in ('add_key','request_key','keyctl') else
           'process_introspection_denied')
    checks[group]=checks[group] and result==-1 and ctypes.get_errno()==errno.EPERM
checks['no_host_routes']=not pathlib.Path('/proc/net/route').read_text().splitlines()[1:]
checks['clean_environment']=(set(os.environ)<= {'HOME','TMPDIR','PATH','LANG','LC_CTYPE','PWD'}
    and all(os.environ.get(k)==v for k,v in {'HOME':'/nonexistent','TMPDIR':'/work',
        'PATH':'/nonexistent','LANG':'C.UTF-8','PWD':'/work'}.items()))
print(json.dumps(checks,sort_keys=True))
'''


def _probe(command, stage, output_dir, public_request, timeout, memory, seccomp_fd, blocked_syscalls):
    # Canary values/paths go only to trusted probe code; not to the worker.
    canary = stage / 'host-private-canary'
    canary.write_bytes(os.urandom(32))
    private_path = str(canary.resolve())
    with socket.socket() as listener, canary.open('rb') as inherited:
        os.set_inheritable(inherited.fileno(), True)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        probe_request = {'hidden': {'host_private_file': private_path,
                                   'host_proc_root': '/proc/' + str(os.getpid()) + '/root' + private_path},
                         'port': listener.getsockname()[1], 'public_request': public_request,
                         'canary_fd': inherited.fileno(), 'blocked_syscalls': blocked_syscalls, 'seccomp_fd': seccomp_fd}
        for namespace in ('pid', 'net', 'user'):
            probe_request[namespace + '_namespace'] = os.readlink('/proc/self/ns/' + namespace)
        probe_input = stage / 'probe-input.json'
        probe_input.write_text(canonical(probe_request), encoding='utf-8')
        outcome = _capture(command, probe_input, output_dir, 'probe.', min(timeout, 10),
                           65536, memory, {'LANG': 'C.UTF-8', 'RDS_HOST_SECRET_CANARY': canary.read_bytes().hex()},
                           seccomp_fd=seccomp_fd)
    checks = None
    if outcome['returncode'] == 0 and not any(outcome[k] for k in ('error', 'timed_out', 'output_limit')):
        try:
            checks = strict_json((output_dir / 'probe.stdout.bin').read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            pass
    required = {'host_private_file', 'host_proc_root', 'environment', 'pid_namespace', 'net_namespace',
                'user_namespace', 'public_input', 'public_readonly', 'runtime_readonly', 'code_readonly',
                'host_network', 'no_host_routes', 'clean_environment', 'inherited_fd',
                'device_files_readonly', 'shared_memory_readonly', 'anonymous_stdin', 'sockets_denied',
                'socketpair_denied', 'keyring_syscalls_denied', 'io_uring_syscalls_denied',
                'sealed_stdin', 'seccomp_fd_closed', 'key_metadata_hidden', 'reaper_proc_hidden',
                'process_introspection_denied'}
    passed = isinstance(checks, dict) and set(checks) == required and all(v is True for v in checks.values())
    return {'passed': passed, 'checks': checks, 'process': outcome}


def validate_response(raw):
    reply = strict_json(raw.decode('utf-8'))
    if not isinstance(reply, dict) or set(reply) not in (RESPONSE_KEYS, RESPONSE_KEYS | {'jump_use_json'}):
        raise ValueError('Worker response must match the RDS proposal fields')
    if not all(isinstance(v, str) for v in reply.values()) or reply['status'] not in RESPONSE_STATUSES:
        raise ValueError('Invalid worker response values')
    for value in reply.values():
        value.encode('utf-8')  # Reject unpaired surrogates before publishing the result envelope.
    if reply['status'] != 'proposed' and (reply['source'] or reply['policy_json']):
        raise ValueError('Non-proposals cannot carry adoptable source or policy')
    return reply


def run_blind(*, request, worker, runtime, output_dir, public_files=(), private_roots=(),
              timeout_seconds=30, max_output_bytes=MAX_INPUT_BYTES, memory_bytes=512 * 1024 * 1024):
    """Run one Python worker, or retain a refusal; never fall back to host execution.

    runtime = {'python': '/usr/bin/python3.X', 'files': [file_spec, ...]}
    Every file_spec has absolute source, staged path, and SHA-256. Runtime paths
    are absolute; public paths are relative. worker.path must be 'worker.py'.
    output_dir must not exist; it is the immutable attempt boundary, not a mount.
    """
    started = time.monotonic()
    output_dir = Path(output_dir).absolute()
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    if sys.platform == 'linux':
        parent_fd = os.open(output_dir.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    result = {'schema': 1, 'status': 'refused', 'reason': None, 'worker_dispatched': False,
              'isolation': None, 'process': None, 'response': None}
    _record(output_dir / 'attempt.json', {'schema': 1, 'started_at': time.time()})
    try:
        if sys.platform != 'linux':
            raise ValueError('ISOLATION_UNAVAILABLE: Linux bubblewrap is required')
        if os.geteuid() == 0:
            raise ValueError('ISOLATION_UNAVAILABLE: an unprivileged host account is required')
        bwrap = shutil.which('bwrap', path='/usr/bin:/bin')
        if bwrap is None:
            raise ValueError('ISOLATION_UNAVAILABLE: bubblewrap is not installed')
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ValueError('Worker wall allowance must be positive and at most 120 seconds')
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= MAX_INPUT_BYTES:
            raise ValueError('Invalid output byte bound')
        if type(memory_bytes) is not int or not 64 * 1024 * 1024 <= memory_bytes <= 2 * 1024**3:
            raise ValueError('Invalid address-space byte bound')
        result['backend'] = {'kind': 'bubblewrap', 'path': bwrap,
                             'sha256': hashlib.sha256(Path(bwrap).read_bytes()).hexdigest(),
                             'kernel': os.uname().release}
        raw_request = _request_bytes(request)
        private_roots = tuple(Path(p).resolve(strict=True) for p in private_roots)
        if not isinstance(runtime, dict) or set(runtime) != {'python', 'files'} or not isinstance(runtime['files'], list):
            raise ValueError('An explicit pinned clean runtime inventory is required')
        python = str(_destination(runtime['python'], runtime=True))
        if not 1 <= len(runtime['files']) <= MAX_RUNTIME_FILES or len(public_files) > MAX_PUBLIC_FILES:
            raise ValueError('File inventory count exceeds bound')
        if not isinstance(worker, dict) or worker.get('path') != 'worker.py':
            raise ValueError('Worker must have the fixed staged name worker.py')
        seccomp_raw, blocked_syscalls = _seccomp_program()
        result['backend']['seccomp_sha256'] = hashlib.sha256(seccomp_raw).hexdigest()
        with tempfile.TemporaryDirectory(prefix='rds-blind-', dir='/tmp') as temp, \
                os.fdopen(_sealed_fd('rds-blind-seccomp', seccomp_raw), 'rb') as seccomp:
            stage = Path(temp)
            roots = {name: stage / name for name in ('runtime', 'public', 'code')}
            for path in roots.values():
                path.mkdir()
            # Mount points must exist inside the new read-only root.
            for name in ('proc', 'dev', 'public', 'rds', 'work'):
                (roots['runtime'] / name).mkdir()
            total, identities, destinations = 0, [], set()
            for spec in runtime['files']:
                path = _destination(spec['path'], runtime=True)
                if str(path) in destinations:
                    raise ValueError('Duplicate runtime destination')
                destinations.add(str(path))
                raw = _read_pinned(spec, private_roots, MAX_RUNTIME_BYTES - total)
                total += len(raw)
                _stage_file(roots['runtime'], path, raw, executable=True)
                identities.append({'path': str(path), 'sha256': spec['sha256']})
            if python not in destinations:
                raise ValueError('Python executable is absent from the pinned runtime inventory')
            _stage_file(roots['code'], PurePosixPath('worker.py'), _read_pinned(worker, private_roots, MAX_INPUT_BYTES))
            _stage_file(roots['code'], PurePosixPath('probe.py'), _PROBE.encode('utf-8'))
            _stage_file(roots['code'], PurePosixPath('empty'), b'')
            (roots['code'] / 'empty-proc-one').mkdir(mode=0o555)
            _stage_file(roots['public'], PurePosixPath('request.json'), raw_request)
            public_identities = []
            public_total = len(raw_request)
            for spec in public_files:
                path = _destination(spec['path'])
                raw = _read_pinned(spec, private_roots, MAX_INPUT_BYTES - public_total)
                public_total += len(raw)
                _stage_file(roots['public'], path, raw)
                public_identities.append({'path': str(path), 'sha256': spec['sha256']})
            result['inputs'] = {'request_sha256': hashlib.sha256(raw_request).hexdigest(),
                                'worker_sha256': worker['sha256'], 'runtime': identities, 'runtime_python': python,
                                'public_files': public_identities}
            _write_record(output_dir / 'request.json', raw_request)
            base = (bwrap, roots['runtime'], roots['public'], roots['code'], python)
            result['isolation'] = _probe(_command(*base, '/rds/probe.py', seccomp.fileno()), stage, output_dir,
                                         request, timeout_seconds, memory_bytes, seccomp.fileno(), blocked_syscalls)
            if not result['isolation']['passed']:
                result['reason'] = 'ISOLATION_UNAVAILABLE: live boundary probe did not pass; worker was not launched'
            else:
                # Persist intent before execution. No retries or adoption are performed here.
                _record(output_dir / 'dispatch.json', {
                    'inputs_sha256': hashlib.sha256(canonical(result['inputs']).encode('utf-8')).hexdigest(),
                    'backend': result['backend'], 'timeout_seconds': timeout_seconds,
                    'memory_bytes': memory_bytes, 'max_output_bytes': max_output_bytes})
                result['worker_dispatched'] = True
                result['process'] = _capture(_command(*base, '/rds/worker.py', seccomp.fileno()), output_dir / 'request.json',
                                             output_dir, '', timeout_seconds, max_output_bytes, memory_bytes,
                                             seccomp_fd=seccomp.fileno())
                process = result['process']
                result['status'] = 'error'
                if process['error']:
                    result['reason'] = process['error']
                elif process['output_limit']:
                    result['reason'] = 'WORKER_OUTPUT_LIMIT: retained stream prefixes are truncated'
                elif process['timed_out']:
                    result['reason'] = 'WORKER_TIMEOUT'
                elif process['returncode'] != 0:
                    result['reason'] = 'WORKER_NONZERO_EXIT'
                else:
                    try:
                        result['response'] = validate_response((output_dir / 'stdout.bin').read_bytes())
                    except (ValueError, UnicodeError, RecursionError) as exc:
                        result['reason'] = 'INVALID_WORKER_RESPONSE: ' + str(exc)
                    else:
                        result['status'] = 'completed'
                        result['reason'] = 'Bounded worker response collected; scientific correctness is not evaluated'
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError, AttributeError) as exc:
        result['reason'] = str(exc)
        if result['worker_dispatched']:
            result['status'] = 'error'
    # Preserve a complete evidence layout for refusal and errors as well as success.
    for name in ('stdout.bin', 'stderr.bin', 'probe.stdout.bin', 'probe.stderr.bin'):
        path = output_dir / name
        if not path.exists():
            path.write_bytes(b'')
    result['total_wall_seconds'] = time.monotonic() - started
    _record(output_dir / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        raw = read_regular_bytes(args.manifest, MAX_INPUT_BYTES, label='Manifest')
        manifest = strict_json(raw.decode('utf-8'))
        required = {'schema', 'request', 'worker', 'runtime'}
        optional = {'public_files', 'private_roots', 'timeout_seconds', 'max_output_bytes', 'memory_bytes'}
        if not isinstance(manifest, dict) or not required <= set(manifest) or not set(manifest) <= required | optional:
            raise ValueError('Invalid blind-runner manifest fields')
        schema = manifest.pop('schema')
        if type(schema) is not int or schema != 1:
            raise ValueError('Unsupported blind-runner schema')
        result = run_blind(output_dir=args.output_dir, **manifest)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        print(canonical({'status': 'refused', 'reason': str(exc)}))
        return 2
    print(canonical({'status': result['status'], 'reason': result['reason'], 'output_dir': str(args.output_dir)}))
    return 0 if result['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
