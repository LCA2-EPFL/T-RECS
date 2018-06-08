#!/usr/bin/env python3

import capnp
import csv
import logging
import netifaces
import sys
from datetime import datetime
from scapy.all import IP, UDP, Ether, Raw, sniff


logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger('router')

capnp.remove_import_hook()
schema = capnp.load('schema.capnp')


log_file = open("../../output/csv/messages.csv", 'w', buffering=1, newline='')
log_writer = csv.writer(log_file)
log_writer.writerow(['Timestamp', 'Source', 'Destination', 'Message'])

def extract_macs():
    return [
        netifaces.ifaddresses(interface)[netifaces.AF_LINK][0]['addr']
        for interface in netifaces.interfaces() if interface != 'lo'
    ]
# Extract the MAC addresses from every interface and the name mappings.
macs = extract_macs()

def process(packet):
    print(packet)
    global macs
    if packet.haslayer(IP):
        src = packet[IP].src
        sport = packet[UDP].sport
        dst = packet[IP].dst
        dport = packet[UDP].dport

        # Only incoming packets are logged.
        if packet[Ether].dst in macs:
            message = packet[Raw].load
            try:
                message = schema.Message.from_bytes_packed(message)
            except:
                pass
            print(message)
            log_writer.writerow([datetime.now(), "{}:{}".format(src, sport),
                                 "{}:{}".format(dst, dport), message])


def main():
    # Sniff.
    # https://stackoverflow.com/questions/28292224/scapy-packet-sniffer-triggering-an-action-up-on-each-sniffed-packet
    sniff(filter='udp', prn=process, store=0)

    return 0


if __name__ == '__main__':
    sys.exit(main())
