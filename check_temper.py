#!/usr/bin/env python3

'''
Checks temperature from USB dongle

@see https://github.com/padelt/temper-python
@author Gabriele Tozzi <gabriele@tozzi.eu>
'''

import sys
import time
import temperusb
import argparse

OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3

try:
    parser = argparse.ArgumentParser(description='Check temperature sensor')
    parser.add_argument('-w', dest='warn', type=int, default=25)
    parser.add_argument('-c', dest='crit', type=int, default=35)
    args = parser.parse_args()

    th = temperusb.TemperHandler()
    devs = th.get_devices()
    if not len(devs):
        print('UNKNOWN')
        print('Error: device not found')
        sys.exit(UNKNOWN)

    dev = devs[0]
    t = dev.get_temperatures()[0]['temperature_c']

    if t >= args.crit:
        status = 'CRITICAL'
        code = CRITICAL
    elif t >= args.warn:
        status = 'WARNING'
        code = WARNING
    else:
        status = 'OK'
        code = OK

    print(status)
    print('Temperature: {}°C'.format(t))
    sys.exit(code)
except Exception as e:
    print('UNKNOWN')
    print('Error: {}'.format(e))
    sys.exit(UNKNOWN)

