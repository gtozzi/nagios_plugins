#!/usr/bin/env python3

'''
Checks various BTRFS params

@author Gabriele Tozzi <gabriele@tozzi.eu>
'''

import os
import re
import sys
import enum
import json
import typing
import logging
import datetime
import subprocess


class IcingaStatus(enum.IntEnum):
	OK = 0
	WARNING = 1
	CRITICAL = 2
	UNKNOWN = 3


class PerfDataRow:
	""" @see https://www.monitoring-plugins.org/doc/guidelines.html#AEN201 """

	def __init__(self, label:str, value:float, uom:str|None=None,
			warn:float|None=None, crit:float|None=None, min:float|None=None, max:float|None=None) -> None:
		self.label = label
		self.value = value
		self.uom = uom
		self.warn = warn
		self.crit = crit
		self.min = min
		self.max = max

	def __str__(self) -> str:
		""" 'label'=value[UOM];[warn];[crit];[min];[max] """
		if ' ' in self.label:
			out = f"'{self.label}'"
		else:
			out = self.label
		out += f"={self.value}"
		if self.uom:
			out += self.uom
		out += ';'
		if self.warn:
			out += f'{self.warn}'
		out += ';'
		if self.crit:
			out += f'{self.crit}'
		out += ';'
		if self.min:
			out += f'{self.min}'
		out += ';'
		if self.max:
			out += f'{self.max}'
		return out


class CheckResult(typing.NamedTuple):
	status:IcingaStatus
	message:str|None = None
	perf_data:list[PerfDataRow]|None = None


class BtrfChecker:

	# Scrub regexes
	SCRUB_STARTED_RE = re.compile('^Scrub started:\s+([A-Za-z]{3}\s+[A-Za-z]{3}\s+[0-9]{1,2}\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s+[0-9]{4})$', re.M)
	STATUS_RE = re.compile('^Status:\s+([a-z]+)$', re.M)
	DURATION_RE = re.compile('^Duration:\s+([0-9]+):([0-9]{1,2}):([0-9]{1,2})$', re.M)
	PERF_ROW_RE = re.compile('^\s*([a-z_]+):\s*([0-9]+)$', re.M)

	def __init__(self, volume_path:str) -> None:
		self.log = logging.getLogger('btrfscheck')
		self.volume_path = volume_path
		self.is_root = os.geteuid() == 0

		cmd = ('which', 'btrfs')
		self.btrfs_path = subprocess.check_output(cmd, text=True).strip()

	def __run_cmd(self, cmd:list[str]) -> subprocess.CompletedProcess:
		if not self.is_root:
			cmd = [ 'sudo' ] + cmd
		res = subprocess.run(cmd, capture_output=True, text=True)
		self.log.debug('STDOUT: %s', res.stdout)
		self.log.debug('STDERR: %s', res.stderr)
		return res

	def check_last_scrub_started(self, warn_days:int, crit_days:int) -> CheckResult:
		''' Checks last scrub started date '''
		cmd = [ self.btrfs_path, 'scrub', 'status', '-R', self.volume_path ]
		res = self.__run_cmd(cmd)

		m = self.SCRUB_STARTED_RE.search(res.stdout)
		if not m:
			return CheckResult(IcingaStatus.UNKNOWN, f'Unparsable stdout, {res.stderr}', [])

		try:
			scrub_start = datetime.datetime.strptime(m.group(1), r'%a %b %d %H:%M:%S %Y')
		except ValueError:
			return CheckResult(IcingaStatus.UNKNOWN, f'Unparsable datetime, {m.group(1)}', [])

		now = datetime.datetime.now()
		warn = now - datetime.timedelta(days=warn_days)
		crit = now - datetime.timedelta(days=crit_days)
		self.log.debug('Started: %s, warn: %s, crit: %s', scrub_start, warn, crit)
		if scrub_start < crit:
			status = IcingaStatus.CRITICAL
		elif scrub_start < warn:
			status = IcingaStatus.WARNING
		else:
			status = IcingaStatus.OK

		perf_data:list[PerfDataRow] = []

		m = self.DURATION_RE.search(res.stdout)
		if not m:
			self.log.warning('Could not find duration')
		else:
			hours, mins, secs = m.groups()
			try:
				perf_data.append(PerfDataRow('duration', int(secs) + int(mins) * 60 + int(hours) * 60 * 60, 's'))
			except ValueError:
				self.log.warning('Could not parse duration')

		text_status = None
		m = self.STATUS_RE.search(res.stdout)
		if not m:
			self.log.warning('Could not find status')
		else:
			text_status = m.group(1)

		for m in self.PERF_ROW_RE.finditer(res.stdout):
			try:
				perf_data.append(PerfDataRow(m.group(1), int(m.group(2))))
			except ValueError:
				self.log.warning('Could not parse perfdata row')

		self.log.debug('Perf data: %s', perf_data)
		return CheckResult(status, text_status, perf_data)

	def check_device_stats(self, warn_count:int, crit_count:int) -> CheckResult:
		''' Checks device statistics '''
		cmd = [ self.btrfs_path, '--format', 'json', 'device', 'stats', self.volume_path ]
		res = self.__run_cmd(cmd)

		try:
			data = json.loads(res.stdout)
		except json.JSONDecodeError:
			return CheckResult(IcingaStatus.UNKNOWN, f'Unparsable json stdout, {res.stderr}', [])

		errors:list[str] = []
		status = IcingaStatus.OK
		perf_data:list[PerfDataRow] = []
		for device in data['device-stats']:
			device_name = device['device']
			device_id = device['devid']
			for key, val in device.items():
				if not key.endswith('_errs'):
					continue

				err_count = int(val)

				if err_count > crit_count:
					status = IcingaStatus.CRITICAL
				elif err_count > warn_count and status != IcingaStatus.CRITICAL:
					status = IcingaStatus.WARNING

				if err_count > 0:
					errors.append(f'{device_name} {key}: {err_count}')

				perf_data.append(PerfDataRow(f'dev{device_id}_{key}', err_count, 'c'))

		return CheckResult(status, ', '.join(errors), perf_data)


if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument('-v', '--verbose', action='store_true', help='show debug output')
	parser.add_argument('volume_path', help='path of the mount point to check')
	subparsers = parser.add_subparsers(dest='check', required=True, help='check type')

	parser_lss = subparsers.add_parser('last_scrub_started', help='checks when last scrub has been started for volume')
	parser_lss.add_argument('-w', '--warn', type=int, default=40, help='warning days interval')
	parser_lss.add_argument('-c', '--crit', type=int, default=100, help='critical days interval')

	parser_lss = subparsers.add_parser('device_stats', help='checks device statistic counters')
	parser_lss.add_argument('-w', '--warn', type=int, default=0, help='warning count')
	parser_lss.add_argument('-c', '--crit', type=int, default=0, help='critical count')

	args = parser.parse_args()

	if args.verbose:
		ll = logging.DEBUG
		logging.basicConfig(level=ll)

	try:
		bc = BtrfChecker(args.volume_path)

		if args.check == 'last_scrub_started':
			res = bc.check_last_scrub_started(args.warn, args.crit)
		elif args.check == 'device_stats':
			res = bc.check_device_stats(args.warn, args.crit)
		else:
			raise NotImplementedError(args.check)

		if res.message is None or res.message == '':
			print(res.status.name)
		else:
			print(f'{res.status.name}: {res.message}')
		if res.perf_data:
			print('|' + ' '.join(map(str,res.perf_data)))
		sys.exit(int(res.status))

	except Exception as e:
		logging.exception('Got exception')
		print('Got Exception: ', e)
		sys.exit(int(IcingaStatus.UNKNOWN))

	sys.exit(int(IcingaStatus.UNKNOWN))
