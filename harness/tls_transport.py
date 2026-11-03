#!/usr/bin/env python3
"""Ephemeral mutually authenticated worker transport; SSH is used only to provision."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.coupled import receive, send


class TLSRemote:
    def __init__(self, endpoint, settings, init):
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=settings['ca'])
        context.load_cert_chain(settings['certificate'], settings['key'])
        plain = socket.create_connection((endpoint['host'], endpoint['port']), timeout=30)
        plain.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection = context.wrap_socket(plain, server_hostname=endpoint['name'])
        self.connection.settimeout(settings.get('rpc_timeout_seconds', 600))
        self.reader = self.connection.makefile('rb')
        self.writer = self.connection.makefile('wb')
        try:
            response = self.call({'action': 'init', **init})
            expected = init['expected']
            if any(response[key] != expected[key] for key in ('grid', 'catalog', 'source', 'dependencies')):
                raise ValueError('TLS worker inputs or dependencies differ from coordinator')
        except BaseException:
            self.close()
            raise

    def call(self, message):
        send(self.writer, message)
        response = receive(self.reader)
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response

    def close(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.reader.close()
        self.writer.close()
        self.connection.close()


def read_exact(stream, size):
    parts = []
    while size:
        part = stream.read(size)
        if not part:
            raise EOFError('Connection closed')
        parts.append(part)
        size -= len(part)
    return b''.join(parts)


def packet(stream):
    header = read_exact(stream, 8)
    size = struct.unpack('!Q', header)[0]
    if size > 1024 ** 3:
        raise ValueError('Frame exceeds 1 GiB bound')
    return header + read_exact(stream, size)


def session(connection, python, runner):
    process = None
    try:
        # Each authenticated session gets a separate Python compute process;
        # the TLS service threads only forward frames and never share a Shard.
        process = subprocess.Popen([python, str(runner), '--worker'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        with connection.makefile('rb') as reader, connection.makefile('wb') as writer:
            while True:
                command = packet(reader)
                process.stdin.write(command)
                process.stdin.flush()
                writer.write(packet(process.stdout))
                writer.flush()
    except (EOFError, OSError, ValueError) as error:
        if not isinstance(error, EOFError):
            print(f'Worker session ended: {type(error).__name__}', file=sys.stderr, flush=True)
    finally:
        connection.close()
        if process:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)


def serve(args):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.certificate, args.key)
    context.load_verify_locations(args.ca)
    context.verify_mode = ssl.CERT_REQUIRED
    threads = []
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.bind, args.port))
        listener.listen(64)
        listener.settimeout(1)
        state = {'pid': os.getpid(), 'port': listener.getsockname()[1], 'ready': True}
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state) + '\n')
        while True:
            try:
                plain, _peer = listener.accept()
            except socket.timeout:
                continue
            plain.settimeout(15)
            plain.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                connection = context.wrap_socket(plain, server_side=True)
            except OSError:
                plain.close()
                continue
            connection.settimeout(1800)
            thread = threading.Thread(target=session, args=(connection, args.python, args.runner), daemon=True)
            thread.start()
            threads.append(thread)
            threads = [item for item in threads if item.is_alive()]


def supervisor(args):
    directory = args.directory.resolve()
    with (directory / 'controller.log').open('a') as log:
        process = subprocess.Popen([args.python, str(directory / 'harness/coupled.py'),
                                   *json.loads((directory / 'controller-args.json').read_text())],
                                  stdout=log, stderr=subprocess.STDOUT)
        status = {'pid': os.getpid(), 'child_pid': process.pid, 'state': 'running'}
        (directory / 'controller-status.json').write_text(json.dumps(status) + '\n')
        status.update(state='completed' if process.wait() == 0 else 'failed', exit_code=process.returncode)
        (directory / 'controller-status.json').write_text(json.dumps(status) + '\n')


def certificates(directory, workers):
    """Create per-run identities; the CA private key never leaves this directory."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    def run(*command):
        subprocess.run(['openssl', *map(str, command)], check=True, capture_output=True)
    ca_key, ca_cert = directory / 'ca.key', directory / 'ca.crt'
    run('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3',
        '-keyout', ca_key, '-out', ca_cert, '-subj', '/CN=Bratos ephemeral run CA')
    for name, purpose in [('controller', 'clientAuth')] + [(f'worker-{i}', 'serverAuth') for i in range(workers)]:
        key, csr, certificate = directory / f'{name}.key', directory / f'{name}.csr', directory / f'{name}.crt'
        extension = directory / f'{name}.extensions'
        extension.write_text(f'basicConstraints=CA:FALSE\nextendedKeyUsage={purpose}\nsubjectAltName=DNS:{name}\n')
        run('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', csr, '-subj', f'/CN={name}')
        run('x509', '-req', '-in', csr, '-CA', ca_cert, '-CAkey', ca_key, '-CAcreateserial',
            '-out', certificate, '-days', '3', '-extfile', extension)
    for key in directory.glob('*.key'):
        key.chmod(0o600)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    server = commands.add_parser('serve')
    server.add_argument('--ca', type=Path, required=True)
    server.add_argument('--certificate', type=Path, required=True)
    server.add_argument('--key', type=Path, required=True)
    server.add_argument('--bind', default='0.0.0.0')
    server.add_argument('--port', type=int, default=0)
    server.add_argument('--state', type=Path, required=True)
    server.add_argument('--python', default=sys.executable)
    server.add_argument('--runner', type=Path, default=ROOT / 'harness/coupled.py')
    monitor = commands.add_parser('supervise')
    monitor.add_argument('directory', type=Path)
    monitor.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    if args.action == 'serve':
        serve(args)
    else:
        supervisor(args)
