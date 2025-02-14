# -*- coding: utf-8 -*-
'''
    :codeauthor: Pedro Algarvio (pedro@algarvio.me)
    :copyright: Copyright 2015 by the SaltStack Team, see AUTHORS for more details.
    :license: Apache 2.0, see LICENSE for more details.


    pytestsalt.engines.pytest_engine
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Simple salt engine which will setup a socket to accept connections allowing us to know
    when a daemon is up and running
'''

# Import python libs
from __future__ import absolute_import, print_function, unicode_literals
import sys
import errno
import socket
import logging

# Import salt libs
import salt.utils.event
import salt.utils.asynchronous

# Import 3rd-party libs
from tornado import gen
from tornado import ioloop
from tornado import netutil
from tornado import iostream

log = logging.getLogger(__name__)

__virtualname__ = 'salt_runtests'


def __virtual__():
    return 'runtests_conn_check_port' in __opts__  # pylint: disable=undefined-variable


def start():
    pytest_engine = PyTestEngine(__opts__)  # pylint: disable=undefined-variable
    pytest_engine.start()


class PyTestEngine(object):
    def __init__(self, opts):
        self.opts = opts
        self.sock = None

    def start(self):
        self.io_loop = ioloop.IOLoop()
        self.io_loop.make_current()
        self.io_loop.add_callback(self._start)
        self.io_loop.start()

    @gen.coroutine
    def _start(self):
        self.io_loop.spawn_callback(self.fire_master_started_event)
        port = int(self.opts['runtests_conn_check_port'])
        log.info('Starting Pytest Engine(role=%s) on port %s', self.opts['__role'], port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(0)
        # bind the socket to localhost on the config provided port
        self.sock.bind(('localhost', port))
        # become a server socket
        self.sock.listen(5)
        with salt.utils.asynchronous.current_ioloop(self.io_loop):
            netutil.add_accept_handler(
                self.sock,
                self.handle_connection,
            )

    def handle_connection(self, connection, address):
        log.warning('Accepted connection from %s. Role: %s', address, self.opts['__role'])
        # We just need to know that the daemon running the engine is alive...
        try:
            connection.shutdown(socket.SHUT_RDWR)  # pylint: disable=no-member
            connection.close()
        except socket.error as exc:
            if not sys.platform.startswith('darwin'):
                raise
            try:
                if exc.errno != errno.ENOTCONN:
                    raise
            except AttributeError:
                # This is not macOS !?
                pass

    @gen.coroutine
    def fire_master_started_event(self):
        log.info('Firing salt-master started event...')
        event_bus = salt.utils.event.get_master_event(self.opts, self.opts['sock_dir'], listen=False)
        master_start_event_tag = 'salt/master/{0}/start'.format(self.opts['id'])
        load = {'id': self.opts['id'], 'tag': master_start_event_tag, 'data': {}}
        # One minute should be more than enough to fire these events every second in order
        # for pytest-salt to pickup that the master is running
        timeout = 60
        while True:
            timeout -= 1
            try:
                event_bus.fire_event(load, master_start_event_tag, timeout=500)
                if timeout <= 0:
                    break
                yield gen.sleep(1)
            except iostream.StreamClosedError:
                break
