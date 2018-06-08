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

# TODO Add a log generator function?

import math
import random
from time import sleep, time, localtime, asctime
import socket
import json
from threading import Thread, Timer, Lock
import sys
import argparse
import csv
import logging
from timeit import default_timer

from snippets import load_json_file, load_json_data, dump_json_data, load_api
from ev import EV


SIMULATION_FROM_ONE_DAY_ARRIVAL_TRACE = True

BUFFER_LIMIT = 10000 # bytes
UDP_PORT_CSA_LISTENS_CSM_ARRIVAL_DEPARTURE_EVENTS = 32012

MEASUREMENT_UPDATE_PERIOD = 0.05 # seconds
#MEASUREMENT_UPDATE_PERIOD = 5 # seconds (for testing)

SIM_START_TIME = 0  # simulation start time: no of minutes after midnight the simulation starts. This time matters because the arrival rate depends on the time of the day.

NO_CHARGING_SLOTS = 400  # number of EVs that can be charged simultaeneously

# for the moment, EV specific values are same for all EVs. We can then later define the type of EVs and these values specific to those types of EVs.
EV_CHARGING_MIN_P = 500  # watts,

EV_CHARGING_MAX_P = 21120  # watts, value corresponds to the full power charging type suggestion from CLP Hong Kong, i.e., 220 volts, 32 amps considering 3 phase charging
#EV_CHARGING_MAX_P = 20000  # watts, for simplified debugging

EV_CHARGING_STAY_TIME = EV_CHARGING_DEPARTURE_TIME = 14400  # seconds, or 4 hours (stay time is the same as departure time for now. However, they do not need to be the same.
# Moreover, stay time and departure time should be randomized in a range.)

#EV_CHARGING_STAY_TIME = EV_CHARGING_DEPARTURE_TIME = 100 # for testing purposes

EV_ENERGY_CAPACITY = 100  # kWh, Tesla cars have 75 kWh, 100kWh battery energy capacity.

EV_SOE_INITIAL = 0.1  # this should be randomized in a range.

EV_SOE_TARGET = 1.0  # this should be randomized in a range.

EV_ENERGY_DEMAND = (
    EV_SOE_TARGET - EV_SOE_INITIAL
) * EV_ENERGY_CAPACITY  # Currently, 90 kWh but EV can only be supplied with 84,480 kWH energy if charged at max power 21,120 watts for 4 hours duration.
# This means charging station will not have any flexibility.

EV_INITIAL_DELAY = 0.1  # seconds, the time EV takes to start implementing the requested setpoint

# I WILL TAKE EV_LOCK_TIME AS 1.
EV_LOCK_TIME = 1  # seconds, for the moment, the lock time is more than the sum of initial delay as well as the maximum ramping time.
# However, it can be less than this time and in that case, I need to change the logic of updating the implemented setpoint when I receive the requestSetpoint.
# This is TODO but I guess, can easily be done. In this case, I cannot just set the impleneted setpoint as requested setpoint but I need to compute and put the real implemented setpoint.

EV_RAMPING_TIME_MAX_P = 0.8  # secs (to go from 0 to Pmax in our case)


logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
logger = logging.getLogger('resource.evcs')

lock = Lock()
occupied_slots = {}

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setblocking(False)


def departure_ev_event(slot_id, arriv_depart_addr):
    global occupied_slots

    if occupied_slots[slot_id]:
        del occupied_slots[slot_id]

        print ('DEBUG: EV at slot {} departed and the "departure" message is sent to CSA.'.format(slot_id))

        message = {'event': 'departure', 'slotId': slot_id}
        sock.sendto(
            json.dumps(message).encode('utf-8'),
            arriv_depart_addr)
    else:
        print ('DEBUG: ATTENTION! ATTENTION! ATTENTION! EV at slot {} was already departed. It should not happen.'.format(slot_id))



def simulate_departure(slot_id, arriv_depart_addr):
    lock.acquire()
    print ('DEBUG: It is the time for the EV at slot {} to depart!'.format(slot_id))

    ev = occupied_slots[slot_id]
    ev.isStopped = True
    ev.set_requested_setpoint(0, 0)

    message = {'event': 'stopped_charging', 'slotId': slot_id}
    sock.sendto(
        json.dumps(message).encode('utf-8'),
        arriv_depart_addr)

    print ('DEBUG: "stopped_charging" message with slot id = {} is sent to the CSA.'.format(slot_id))
    lock.release()



def execute_arrival_and_send_msg_csa(current_time, arriv_depart_addr):
    global occupied_slots

    slot_to_use = 0

    for slot in range(1, NO_CHARGING_SLOTS + 1):
        if slot not in occupied_slots:
            slot_to_use = slot
            break

    # if all the slots are used, return (it should not happen because already checked!)
    if slot_to_use == 0:
        print ('DEBUG: All slots are already IN USE. So, not proceeding with this arrival!! WARNING!! WARNING!!')
        return

    # creating a new EV object...
    newEV = EV(slot_to_use, EV_CHARGING_MIN_P, EV_CHARGING_MAX_P,
                EV_ENERGY_CAPACITY, EV_SOE_INITIAL, EV_SOE_TARGET,
                EV_ENERGY_DEMAND, current_time,
                EV_CHARGING_STAY_TIME,
                EV_INITIAL_DELAY,
                EV_LOCK_TIME, EV_RAMPING_TIME_MAX_P)

    print('DEBUG: Storing the newly arrived EV at slot id {}'.format(slot_to_use))
    occupied_slots[slot_to_use] = newEV
    print('DEBUG: Total number of occupied slots are now {}'.format(len(occupied_slots)))

    message = {
        'event': 'arrival',
        'slotId': newEV.slot_id,
        'Pmin': newEV.P_min,
        'Pmax': newEV.P_max,
        'stay_time': newEV.stay_time,
        'energy_demand': newEV.energy_demand
    }

    sock.sendto(
        json.dumps(message).encode(),
        arriv_depart_addr)

    print ("DEBUG: Sent the arrival message to CSA.")

    return slot_to_use, newEV.stay_time


def listen_from_csa(listen_addr):
    print ("DEBUG: The thread to listen for messsages from CSA is started!")

    sock_listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_listen.bind(listen_addr)

    while True:
        data, addr = sock_listen.recvfrom(BUFFER_LIMIT)

        message = json.loads(data.decode('utf-8'))
        if message['event'] == 'command':
            commands = message['commands']
            if len(commands) == 0:
                print ('DEBUG: COMMAND message from CSA is Empty!!!')
                continue

            lock.acquire()

            start_time = time()
            print ("DEBUG: COMMAND message from CSA (lock acquired) is not empty. Current time is {}".format( asctime( localtime( start_time ))))
            for command in commands:
                slot_id = int(command['id'])
                P = command['P']
                Q = command['Q']
                print ("DEBUG: COMMAND slot id: {}, P: {}, and Q: {}".format(slot_id, P, Q))

                if slot_id in occupied_slots:
                    print ("DEBUG: slot id {} is in occupied slots...".format(slot_id))
                    ev = occupied_slots[slot_id]
                    if ev.isStopped == True:
                        print ("DEBUG: This EV was stopped. We CANNOT receive commands anymore for this EV. Neglecting the COMMAND!!!!")

                    elif (ev.P_requested is None or
                            time() > ev.request_time + ev.lock_time):
                        print('DEBUG: EV at slot {} is not locked; updating the requested setpoint for this EV...'
                              .format(slot_id))

                        ev.set_requested_setpoint(P, Q)

                    else:
                        print('DEBUG: EV at slot {} is LOCKED!!!!! Command will NOT be implemented!!!! ATTENTION! ATTENTION!'
                              .format(slot_id)) # We should not receive a command from CSA while the EV is locked.
                else:
                    print ('DEBUG: Received a COMMAND for NON EXISTING EV!!!! ATTENTION! ATTENTION!')

            end_time = time()
            print ("DEBUG: COMMAND message from CSA processed (lock is going to be released) in {} secs.".format(end_time - start_time))

            lock.release()


def update_and_send_measurements(bus_index, api, reply_addr, arriv_depart_addr):
    global occupied_slots

    while True:
        start_time = default_timer()

        reply = {'event': 'measurements', 'measurements': []}
        total_P = 0

        lock.acquire()

        list_departed = []
        for slot_no, ev in occupied_slots.items():
            if ev.P_requested == None:
                continue

            elif ev.P_implemented == ev.P_requested:
                if ev.isStopped == True:
                    # ev.P_requested should be zero in this case...
                    # send a departure message...
                    list_departed.append(slot_no)

                total_P += ev.P_implemented

                # update energy demand remaining
                ev.update_energy_demand_remaining(MEASUREMENT_UPDATE_PERIOD)
                continue
            elif time() < ev.request_time + ev.delay_initial:
                total_P += ev.P_implemented

                # update energy demand remaining
                ev.update_energy_demand_remaining(MEASUREMENT_UPDATE_PERIOD)
                continue
            else:
                # ramp up or down
                ev.ramping(MEASUREMENT_UPDATE_PERIOD)

                total_P += ev.P_implemented

                # update energy demand remaining
                ev.update_energy_demand_remaining(MEASUREMENT_UPDATE_PERIOD)

                reply['measurements'].append({
                    'id': slot_no,
                    'P': ev.P_implemented,
                    'Q': ev.Q_implemented
                })

        for slot_no in list_departed:
            departure_ev_event(slot_no, arriv_depart_addr)

        lock.release()

        print ("DEBUG: CSM measurement update = {}".format(str(reply)))
        # Send the reply.
        sock.sendto(
            json.dumps(reply).encode('utf-8'), reply_addr)
        print ('DEBUG: measurement update is sent to CSA.')

        # Send the new state to the Grid model
        api.implement_setpoint(bus_index, total_P, 0) # Basically, it's the power demand (that's why, load is positive)
        print ('DEBUG: Sent total CS Pd = {}, and Qd = {}, to Grid Module.'.format(total_P, 0))

        elapsed_time = default_timer() - start_time
        if elapsed_time > MEASUREMENT_UPDATE_PERIOD:
            print ('DEBUG: elapsed_time is {} secs. It is greater than MEASUREMENT_UPDATE_PERIOD, which is {}'.format(elapsed_time, MEASUREMENT_UPDATE_PERIOD))
            continue

        sleep(MEASUREMENT_UPDATE_PERIOD - elapsed_time % MEASUREMENT_UPDATE_PERIOD)



def main():
    # Parse the arguments.
    parser = argparse.ArgumentParser(
        description="EV Charging Station that communicates with its resource agent."
    )
    parser.add_argument('config_path',
                        help="Path to the JSON config file for the CS")
    parser.add_argument('--api_path',
                        help="Path to which the GridAPI will be pickled",
                        default='grid_api.pickle')
    parser.add_argument('--params_path',
                        help="Path to file containing the CS parameters",
                        default='evcs_config.json')
    args = parser.parse_args()


    # Load the configuration files.
    config = load_json_file(args.config_path, logger)

    # TODO: Load the params from the config file...
    #params = load_json_file(args.params_path, logger)

    # Load the GridAPI.
    api = load_api(args.api_path)

    # Extract some relevant things out of the configuration.
    bus_index = config['bus_index']
    listen_addr = api.grid_ip, config['port']
    #listen_addr = '127.0.0.1', config['port']
    reply_addr = config['RA']['ip'], config['RA']['port']
    #reply_addr = '127.0.0.1', config['RA']['port']
    arriv_depart_addr = config['RA']['ip'], UDP_PORT_CSA_LISTENS_CSM_ARRIVAL_DEPARTURE_EVENTS
    #arriv_depart_addr = '127.0.0.1', UDP_PORT_CSA_LISTENS_CSM_ARRIVAL_DEPARTURE_EVENTS

    print ("DEBUG: Starting a new thread to listen for COMMANDS from CSA...".format())
    # Run the listener service.
    Thread(target=listen_from_csa, args=(listen_addr, )).start()

    print ("DEBUG: Starting a new thread to periodically update implemented setpoint for each EV and send them to CSA...".format())
    # Run the thread to periodically update implemented setpoint for each EV and send them to CSA...
    Thread(target=update_and_send_measurements, args=(bus_index, api, reply_addr, arriv_depart_addr, )).start()


    if SIMULATION_FROM_ONE_DAY_ARRIVAL_TRACE == True:
        print ('DEBUG: Arrivals from one day trace...')
        # Read arrivals from trace (instead of generating new arrivals each time)
        arrivals = []
        with open('arrivals_in_secs_with_400_max_charging_slots.csv', 'r') as f:
            reader = csv.reader(f)
            arrivals = list(reader)

        last_arrival = 0
        #arrivals = [15, 50, 100, 150, 200, 250, 500] # for testing
        for arrival in arrivals:
            arrival = float(arrival[1])
            time_to_next_arrival = arrival - last_arrival
            last_arrival = arrival
            print('DEBUG: Time to next arrival is {} secs.'
                  .format(time_to_next_arrival))
            print('DEBUG: Going to sleep up to the next arrival...')
            sleep(time_to_next_arrival)
            print("DEBUG: New EV arrived! Current time is {} secs.".format(arrival))

            lock.acquire()
            slot_no, stay_time = execute_arrival_and_send_msg_csa(arrival, arriv_depart_addr)
            lock.release()

            # schedule the departure event
            Timer(stay_time, simulate_departure,
                (slot_no, arriv_depart_addr, )).start()
    else:
        print ('DEBUG: Arrivals from a new infinite non-homogeneous poisson process...')
        # the average rate of arrival of cars per minute
        # in a non-homogeneous Poisson process
        # in a given interval, the rate of arrival is assumed to be
        # constant and independent of each other
        def rate(t):
            t = t % 1440
            if t >= 0 and t < 480:
                hourly_rate = 20
            elif t >= 480 and t < 720:
                hourly_rate = 90
            elif t >= 720 and t < 1080:
                hourly_rate = 30
            elif t >= 1080 and t < 1200:
                hourly_rate = 150
            else:
                hourly_rate = 15
            return hourly_rate / 60

        max_rate = 150 / 60
        print ("DEBUG: Maximum rate of arrival per minute during the day is {}.".format(max_rate))

        current_time = SIM_START_TIME
        print ("DEBUG: Starting the simulation at time {} (number of minutes after the midnight).".format(current_time))
        print ("DEBUG: Occupied slots are initially {}.".format(len(occupied_slots)))

        accumulated_arrival_time = 0
        while True:
            arrival_time = -math.log(1.0 - random.random()) / max_rate
            current_time += arrival_time
            accumulated_arrival_time += arrival_time

            if (random.random() <= rate(current_time) / max_rate
                    and len(occupied_slots) < NO_CHARGING_SLOTS):
                print('DEBUG: Time to next arrival is {} secs.'
                        .format(accumulated_arrival_time * 60))
                print('DEBUG: Going to sleep up to the next arrival...')
                sleep(accumulated_arrival_time * 60)
                print("DEBUG: New EV arrived! Current time is {} mins.".format(current_time))

                lock.acquire()
                slot_no, stay_time = execute_arrival_and_send_msg_csa(current_time, arriv_depart_addr)
                lock.release()

                # schedule the departure event
                Timer(stay_time, simulate_departure,
                    (slot_no, arriv_depart_addr, )).start()

                accumulated_arrival_time = 0


if __name__ == '__main__':
    sys.exit(main())
