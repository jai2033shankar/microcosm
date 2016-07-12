#!/usr/bin/env python

# Copyright 2015 Datawire. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""microcosm.py

Usage:
    microcosm.py [options] <config>
    microcosm.py (-h | --help)
    microcosm.py --version

Options:
    --http-port <port>  The http port to listen on.
    --http-addr <addr>  The http address to bind to [default: 127.0.0.1].
    -h --help           Show the help.
    --version           Show the version.
"""

import logging
import os
import requests
import mdk
import atexit
import traceback

from docopt import docopt
from flask import Flask, jsonify, request
from uuid import uuid4
from microutil import name_version, load_yaml

dependencies = []
m = mdk.init()
node_id = None
service_name = None

app = Flask(__name__)

NODE_REGISTERED              = "*** node started        (service: %s, version: %s, addr: %s)"
NODE_DEPENDS_ON              = "*** node depends on     (%s)"
RECV_REQUEST_MSG             = "--> request             (id: %s)"
SENT_DOWNSTREAM_REQUEST      = "--> downstream request  (service: %s, version: %s, addr: %s)"
RECV_DOWNSTREAM_RESPONSE_MSG = "--> downstream response (id: %s)"
SENT_RESPONSE_MSG            = "<-- response            (id: %s)"


def disable_noisy_loggers(loggers):
    for logger_name in loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)


def configure_dependencies(deps):
    global dependencies
    if isinstance(deps, basestring):
        if deps:
            dependencies = [name_version(d) for d in deps.split(",")]
        else:
            pass
    elif isinstance(deps, list):
        dependencies = [name_version(d) for d in deps]
    else:
        raise TypeError("Invalid config 'dependencies' format. Must either be a list or comma-separated string.")


def is_foundational():
    return len(dependencies) == 0


def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


def do_json():
    request.ssn = m.join(request.headers.get(m.CONTEXT_HEADER))
    ssn = request.ssn
    request_id = ssn.inject()
    ssn.info(service_name, RECV_REQUEST_MSG % request_id)

    result = {
        'node_id': node_id,
        'request_id': request_id,
        'requests': []
    }

    for service, version in dependencies:
        ssn.start_interaction()
        node = ssn.resolve(service, version)
        try:
            response = requests.get(node.address, headers={m.CONTEXT_HEADER: ssn.inject()}, timeout=3.0)
            ssn.info(service_name, SENT_DOWNSTREAM_REQUEST % (node.service, node.version, node.address))
            responder_data = response.json()
            ssn.info(service_name, RECV_DOWNSTREAM_RESPONSE_MSG % responder_data['request_id'])
            result['requests'].append(responder_data)
            ssn.finish_interaction()
        except:
            ssn.fail_interaction("error getting downstream data: " + traceback.format_exc())
            result['requests'].append("ERROR(%s)" % node)

    ssn.info(service_name, SENT_RESPONSE_MSG % request_id)
    return result

@app.route('/')
def process_request():
    return jsonify(do_json())

@app.route('/text')
def text():
    return render(do_json()).strip() + "\n"

def render(result):
    if isinstance(result, basestring):
        return result + "\n"
    node = result["node_id"]
    request_id = result["request_id"]
    requests = "\n".join([render(r) for r in result["requests"]]).replace("\n", "\n  ")
    return node + "\n  " + requests

@app.errorhandler(Exception)
def unhandled_exception(e):
    err = traceback.format_exc(e)
    request.ssn.error(service_name, err)
    return err, 500

def run_server(args):

    config_file = args['<config>']
    config = load_yaml(config_file)

    disable_noisy_loggers(['werkzeug', 'requests', 'urllib3'])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    configure_dependencies(config.get('dependencies', []))

    # These are the host and port the HTTP server is listening on and not the routable host and port
    httpd_config = config.get('http_server', {})
    httpd_addr = httpd_config.get('address', '0.0.0.0')
    httpd_port = int(httpd_config.get('port', args.get('--http-port', 5000)))

    service = config.get('service')
    version = str(config.get('version'))
    address = 'http://{}:{}'.format(httpd_addr, httpd_port)

    global node_id, service_name
    node_id = "%s[%s, %s]" % (service, version, address)
    service_name = service

    m.register(service, version, address)
    m._disco.threshold = 1
    m.start()
    atexit.register(m.stop)

    ssn = m.session()
    ssn.info(service_name, NODE_REGISTERED % (service, version, address))

    if not is_foundational():
        ssn.info(service_name, NODE_DEPENDS_ON % ", ".join(map(str, dependencies)))

    app.run(host=httpd_addr, port=httpd_port, debug=False, threaded=True)

def main():
    exit(run_server(docopt(__doc__, version="microcosm.py 0.0.1")))


if __name__ == '__main__':
    main()
