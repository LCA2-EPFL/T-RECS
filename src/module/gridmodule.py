#!/usr/bin/env python3

# The MIT License (MIT)
#
# Copyright (c) 2018 École Polytechnique Fédérale de Lausanne (EPFL)
# Author: Jagdish P. Achara
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
from csv import DictWriter
from logging import basicConfig, getLogger, INFO
from os import path
from socket import socket, AF_INET, SOCK_DGRAM
from sys import stdout, exit, exc_info
from numpy import maximum, absolute, angle
from datetime import datetime
from gridapi import GridAPI
from multiprocessing import Process, Manager
from singlephasegrid import SinglePhaseGrid
from snippets import load_json_file, load_json_data, dump_json_data, \
    dump_api
from timeit import default_timer as timer
from csv import reader, QUOTE_NONNUMERIC

basicConfig(stream=stdout, level=INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = getLogger('grid.module')


BUFFER_LIMIT = 20000


def extract_state(grid):
    """Extract the state from a grid.

    Parameters
    ----------
        grid : SinglePhaseGrid
            Grid to extract the state from.

    Returns
    -------
        state : dict
            State of the grid after negating the "demand" values.

    """
    grid.computeSlackPower()
    listP = [p for p in grid.pqBusesP]
    listP.insert(0, grid.slackPower[0])
    listQ = [q for q in grid.pqBusesQ]
    listQ.insert(0, grid.slackPower[1])

    grid.computeCurrents()
    lineCurrents = maximum(absolute(grid.forwardCurrents), absolute(grid.backwardCurrents)).tolist()

    # convert real and imaginary to magnitude and angle (degrees)
    Vm = []
    Va = []
    for i in range(len(grid.realV)):
        complexVoltage = complex(grid.realV[i], grid.imagV[i])
        Vm.append(absolute(complexVoltage))
        Va.append(angle(complexVoltage, deg=True))

    return {
        'P': listP,
        'Q': listQ,
        'Vm': Vm,
        'Va': Va,
        'LineCurrents': lineCurrents
    }


def log_generator(state_queue, log_path):
    """Write logs to CSV files, and update it whenever the state is changed.

    Parameters
    ----------
        state_queue : multiprocessing.Queue
            Queue where the state should be put.

        log_path : path_like
            Relative path to which to write the bus and line log.


    """
    log_path_bus = path.join(log_path, 'grid_bus.csv')
    log_path_line = path.join(log_path, 'grid_line.csv')

    log_file_bus = open(log_path_bus, 'w', buffering=1, newline='')
    log_writer_bus = DictWriter(
        log_file_bus, ('Timestamp','BusIndex', 'P', 'Q', 'Vm', 'Va')
    )
    log_writer_bus.writeheader()

    log_file_line = open(log_path_line, 'w', buffering=1, newline='')
    log_writer_line = DictWriter(
        log_file_line, ('Timestamp', 'Line #', 'LineCurrent')
    )
    log_writer_line.writeheader()

    while True:
        # Retrieve the state from the queue.
        state = state_queue.get()
        assert len({
            len(state['P']), len(state['Q']),
            len(state['Vm']), len(state['Va'])
        }) == 1

        row = {'Timestamp': state['Ts']}

        for index, (P, Q, Vm, Va) in enumerate(
                zip(state['P'], state['Q'], state['Vm'], state['Va'])
        ):
            # Write the state of the current bus.
            row.update({
                'BusIndex': index,
                'P': P,
                'Q': Q,
                'Vm': Vm,
                'Va': Va
            })
            log_writer_bus.writerow(row)

        row = {'Timestamp': state['Ts']}

        for index, LineCurrent in enumerate(
                (state['LineCurrents'])
        ):
            # Write the state of the current line.
            row.update({
                'Line #': index,
                'LineCurrent': LineCurrent
            })
            log_writer_line.writerow(row)

    log_writer_bus.close()
    log_writer_line.close()


def update_handler(state, message_queue, state_queue, *args, **kwargs):
    """Handle messages that update the grid, i.e., implement a setpoint.

    Parameters
    ----------
        state : multiprocessing.manager.dict
            Shared dict that stores the state of the grid.

        message_queue : multiprocessing.manager.Queue
            Queue in which the main process stores messages.

        state_queue : multiprocessing.manager.Queue
            Queue in which the updated state will be put.

    Raises
    ------
        error : IOError
            Could not open the trace

        error : ValueError
            Wrong or missing value in the trace


    """

    # Parameters for slack voltage from trace
    use_trace = args[0]['slack_voltage']['use_trace']

    # Load trace
    if use_trace:
        trace_file_path = args[0]['slack_voltage']['trace_file_path']

        try:
            with open(trace_file_path, 'r') as f:
                reader_ = reader(f, quoting=QUOTE_NONNUMERIC)
                slack_voltage = list(reader_)

        except IOError as e:
            logger.error("Could not open {}: {}".format(trace_file_path, e))
            return 1
        except ValueError as e:
            logger.error("ValueError, wrong or missing value in {}: {}".format(trace_file_path, e))
            return 1
        except Exception as e:
            logger.error("Unexpected error", exc_info()[0])
            raise

        # normalize the trace timestamp
        slack_voltage_first_ts = slack_voltage[0][0]
        for i in range(0,len(slack_voltage)):
            slack_voltage[i][0] = slack_voltage[i][0] - slack_voltage_first_ts

        found = False
        end_trace_reach = False
        ptr_ID = -1

        slack_voltage_real = slack_voltage[0][1]
        slack_voltage_imaginary = slack_voltage[0][2]

    else:
        slack_voltage_real = args[0]['slack_voltage']['voltage_real']
        slack_voltage_imaginary = args[0]['slack_voltage']['voltage_imaginary']

    # Initialize the grid.
    grid = SinglePhaseGrid(*args, **kwargs)
    grid.update([0] * (grid.no_buses - 1), [0] * (grid.no_buses - 1), slack_voltage_real, slack_voltage_imaginary)
    state.update(extract_state(grid))
    logger.info("Initial state: {}".format(state))

    state_log = state.copy()
    state_log['Ts'] = datetime.now()
    state_queue.put(state_log)

    reference_time = timer()

    # Process and update messages one by one, and perform load-flow analysis.
    while True:
        msg_qsize = message_queue.qsize()

        if msg_qsize == 0:
            continue

        logger.info("Queue size in update_handler: {}".format(msg_qsize))

        # Get these many messages from the message_queue...
        index_with_updates = {}
        for i in range(msg_qsize):
            msg, _ = message_queue.get()
            bus_index = int(msg['bus_index'])
            Pd, Qd = float(msg['P']), float(msg['Q'])
            index_with_updates[bus_index] = Pd, Qd

        # Construct P, Q lists for doing LF with single update at all buses...
        # P, Q are the three phase voltage
        Pd = []
        Qd = []
        for i in range(1, grid.no_buses):
            if i in index_with_updates:
                Pd_new = index_with_updates[i][0]
                Qd_new = index_with_updates[i][1]
            else:
                Pd_new = grid.pqBusesP[i - 1]
                Qd_new = grid.pqBusesQ[i - 1]
            Pd.append(Pd_new)
            Qd.append(Qd_new)

        # Get Voltage from trace (or use default value) for slack bus...
        if use_trace:
            found = False
            current_time = timer() - reference_time
            delta = abs(current_time - slack_voltage[ptr_ID][0])

            while (not found and not end_trace_reach):

                # reach the end of the trace, take the last element as correct value
                if(ptr_ID + 1) >= len(slack_voltage):
                    end_trace_reach = True
                    # take the last entry
                    ptr_ID = -1

                else:
                    next_delta = abs(current_time - slack_voltage[ptr_ID + 1][0])
                    # the closest value of current_time is at ptr_ID
                    if next_delta > delta:
                        found = True
                    else:
                        delta = next_delta
                        ptr_ID = ptr_ID + 1

            slack_voltage_real = slack_voltage[ptr_ID][1]
            slack_voltage_imaginary = slack_voltage[ptr_ID][2]

        logger.info("Update grid with P, Q ({}, {}) and slack voltage ({}, {}i)".format(Pd, Qd, slack_voltage_real, slack_voltage_imaginary))

        initial_time = datetime.now()

        grid.update(Pd, Qd, slack_voltage_real, slack_voltage_imaginary)  # positive power is generation in grid model except slack bus power.
        logger.info("LF took {} ms".
                    format((datetime.now() - initial_time).total_seconds() * 1e3))

        state.update(extract_state(grid))

        logger.info("Put state onto queue: {}".format(state))

        state_log = state.copy()
        state_log['Ts'] = datetime.now()
        state_queue.put(state_log)


def main():

    # Parse the arguments.
    parser = ArgumentParser(
        description="Grid module that facilitates the operation of the grid."
    )
    parser.add_argument("config_path",
                        help="Path to the JSON config file for the grid",
                        nargs='?')
    parser.add_argument("log_path",
                        help="Path to the log directory for the grid",
                        nargs='?')
    parser.add_argument("grid_module_ip",
                        help="T-RECS (private) IP of the grid module where \
                        it listens for the messages from T-RECS resource models.",
                        nargs='?')
    parser.add_argument("grid_module_port",
                        help="Port where grid module listens for the \
                        messages from T-RECS resource models.",
                        nargs='?')
    parser.add_argument("--api_path",
                        help="Path to which the GridAPI will be pickled",
                        default='grid_api.pickle')
    args = parser.parse_args()

    # Load the configuration file.
    config = load_json_file(args.config_path, logger)

    # Inform the GridAPI of the grid module's address.
    api = GridAPI(args.grid_module_ip, int(args.grid_module_port))
    dump_api(api, args.api_path)
    kwargs = {'api_path': args.api_path}

    # Initialize a multiprocessing manager.
    with Manager() as manager:
        # Shared memory for the state.
        state = manager.dict()

        # Socket to listen for incoming messages.
        sock = socket(AF_INET, SOCK_DGRAM)
        sock.bind((args.grid_module_ip, int(args.grid_module_port)))

        # Handle update messages.
        message_queue = manager.Queue()
        state_queue = manager.Queue()

        Process(target=update_handler,
                args=(state, message_queue, state_queue, config['grid']),
                kwargs=kwargs).start()

        # Log generation.
        Process(target=log_generator, args=(state_queue, args.log_path)).start()

        # Wait for the child process to initialize the grid.
        while not state:
            continue

        while True:
            # The socket listens for messages that ask it to provide its state,
            # or implement a new setpoint.
            data, addr = sock.recvfrom(BUFFER_LIMIT)
            message = load_json_data(data)
            logger.info("Received message from {}: {}".format(addr, message))
            try:
                if message['type'] == 'request':
                    reply = {key: value for key, value in state.items()}
                    logger.info("Send state to {}: {}".format(addr, reply))
                    sock.sendto(dump_json_data(reply), addr)
                elif message['type'] == 'implement_setpoint':
                    logger.info("Implement setpoint: {}".format(message))
                    message_queue.put((message, datetime.now()))
                    logger.info("Queue size: {}".format(message_queue.qsize()))
                else:
                    logger.warn(
                        "Unknown message type: {}".format(message['type']))
            except Exception as e:
                logger.warn("Bad message: {}".format(e))

    return 0


if __name__ == '__main__':
    exit(main())
