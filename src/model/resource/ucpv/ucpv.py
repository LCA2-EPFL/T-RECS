#!/usr/bin/python3

# The MIT License (MIT)
#
# Copyright (c) 2018 École Polytechnique Fédérale de Lausanne (EPFL)
# Author: Jagdish P. Achara and Jérémie Mayor
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from argparse import ArgumentParser
from csv import writer
from logging import basicConfig, INFO, getLogger
from socket import socket, AF_INET, SOCK_DGRAM
from sys import stdout, exit
from datetime import datetime
from multiprocessing import Process, Queue
from snippets import load_json_file, dump_json_data, load_api
from threading import Thread
from time import sleep
from timeit import default_timer
from csv import reader, QUOTE_NONNUMERIC


basicConfig(stream=stdout, level=INFO,
            format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
logger = getLogger('resource.ucpv')

BUFFER_LIMIT = 1024


def reply(addr, state):
    """Reply to the resource agent with the PV's state.

    Parameters
    ----------
        addr : tuple
            Address of the RA.

        state : dict
            State of the UCPV.

    """
    message = {
        'P': state['P'],
        'Q': state['Q']
    }
    logger.info("Sending setpoint (P = {}, Q = {}) to RA"
                .format(message['P'], message['Q']))
    data = dump_json_data(message)
    sock = socket(AF_INET, SOCK_DGRAM)
    sock.sendto(data, addr)


def send(addr, state, period):
    """Periodically send the state to the resource agent.

    Parameters
    ----------
        addr : tuple
            Address of the RA.

        state : dict
            State of the UCPV.

        period : float
            Period with which to send.

    """
    while True:
        start_time = default_timer()
        reply(addr, state)
        elapsed_time = default_timer() - start_time
        logger.info("elapsed time is {}secs, period is {}secs".format(elapsed_time, period))
        if elapsed_time < period:
            logger.info("Going to sleep for {} secs".format(period - elapsed_time))
            sleep(period - elapsed_time)


def generate_log(queue, log_path):
    """Write a log to a CSV file, updated whenever the state is changed.

    Parameters
    ----------
        queue : multiprocessing.Queue
            Queue where the state should be put.

        log_path : path_like
            Relative path to which to write the log.

    """
    with open(log_path, 'w', buffering=1, newline='') as log_file:
        log_writer = writer(log_file)
        log_writer.writerow(['Timestamp', 'P', 'Q'])

        while True:
            state = queue.get()
            log_writer.writerow([state['Ts'], state['P'], state['Q']])


def main():
    # Parse the arguments.
    parser = ArgumentParser(
        description="UCPV that communicates with its resource agent."
    )
    parser.add_argument('config_path',
                        help="Path to the JSON config file for the UCPV")
    parser.add_argument('--api_path',
                        help="Path to which the GridAPI will be pickled",
                        default='grid_api.pickle')
    parser.add_argument('--params_path',
                        help="Path to file containing the UCPV parameters")
    args = parser.parse_args()

    # Load the configuration files.
    params = {}
    config = load_json_file(args.config_path, logger)
    if args.params_path is None:
        params = config
    else:
        params = load_json_file(args.params_path, logger)

    # Extract some relevant things out of the configuration.
    bus_index = config['bus_index']
    irradiance_path = config['irradiance_trace_file_path']

    ucpv_ra_addr = config['RA']['ip'], config['RA']['listen_port']

    log_path = config['log_path']

    # Read in the UCPV parameters.
    update_period = params['update_period'] / 1e3  # convert to seconds from milli seconds
    rated_power_dc_side = params['rated_power_dc_side']
    converter_efficiency = params['converter_efficiency']
    S_STC = params['S_STC']  # Standard test condition

    try:
        with open(irradiance_path, 'r') as f:
            reader_ = reader(f, quoting=QUOTE_NONNUMERIC)
            irradiance = list(reader_)
    except IOError as e:
        logger.error("Could not open {}: {}".format(irradiance_path, e))
        return 1
    except ValueError as e:
        logger.error("ValueError, wrong or missing value in {}: {}".format(irradiance_path, e))
        return 1

    # normalize the trace timestamp
    first_irradiance_ts = irradiance[0][0]
    for i in range(0,len(irradiance)):
        irradiance[i][0] = irradiance[i][0] - first_irradiance_ts

    state_queue = Queue()
    state = {
        'P': 0,
        'Q': 0,
        'Ts':0
    }

    # Load the GridAPI.
    api = load_api(args.api_path)

    # Communicate with the RA.
    Thread(target=send, args=(ucpv_ra_addr, state, update_period)).start()

    # Run the log generation.
    Process(target=generate_log, args=(state_queue, log_path)).start()

    state['Ts'] = datetime.now()
    state_queue.put(state)

    ptr_ID = 0
    P = irradiance[ptr_ID][1] * rated_power_dc_side / S_STC * converter_efficiency
    reference_time = default_timer()
    sleep_time = 0

    while True:

        state['P'] = P

        logger.info("Implementing setpoint at index {}, (Pd = {}, Qd = {})"
                    .format(ptr_ID, state['P'], state['Q']))

        api.implement_setpoint(bus_index, state['P'], state['Q'])

        state['Ts'] = datetime.now()
        state_queue.put(state)

        if(ptr_ID + 1) >= len(irradiance):

            ptr_ID = 0
            P = irradiance[ptr_ID][1] * rated_power_dc_side / S_STC * converter_efficiency
            sleep(sleep_time)
            reference_time = default_timer()
            continue

        else:
            ptr_ID = ptr_ID + 1
            next_sample_time = irradiance[ptr_ID][0]

            P = irradiance[ptr_ID][1] * rated_power_dc_side / S_STC * converter_efficiency
            sleep_time = next_sample_time - (default_timer() - reference_time)

        if sleep_time > 0:
            sleep(sleep_time)


if __name__ == '__main__':
    exit(main())
