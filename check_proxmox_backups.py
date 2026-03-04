#!/usr/bin/env python3

'''
	This plugin checks VM backup status in a Proxmox VE server

	Return codes are:
	0   OK
	1   WARNING
	2   CRITICAL
	3   UNKNOWN

	Return text is:
	TEXT OUTPUT
	[LONG TEXT LINE 1]
	[LONG TEXT LINE 2]
	[LONG TEXT LINE ...]

	@author: Gabriele Tozzi <gabriele@tozzi.eu>

	This program is free software: you can redistribute it and/or modify
	it under the terms of the GNU General Public License as published by
	the Free Software Foundation, either version 3 of the License, or
	(at your option) any later version.

	This program is distributed in the hope that it will be useful,
	but WITHOUT ANY WARRANTY; without even the implied warranty of
	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
	GNU General Public License for more details.

	You should have received a copy of the GNU General Public License
	along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

import sys
import ssl
import time
import pprint
import socket
import typing
import logging
import hashlib
import datetime
import traceback
import threading
import functools
import urllib.parse

OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3

try:
	import requests
	import requests.adapters
	import requests.exceptions
except ModuleNotFoundError:
	print('UNKNOWN')
	print('python3-requests library is not installed')
	sys.exit(UNKNOWN)


class FingerprintAdapter(requests.adapters.HTTPAdapter):
	''' Adapter for checking SSL fingerprint '''
	def __init__(self, fingerprint:str, **kwargs):
		self.fingerprint = fingerprint
		super().__init__(**kwargs)

	def init_poolmanager(self, *args, **kwargs):
		kwargs['assert_fingerprint'] = self.fingerprint
		return super().init_poolmanager(*args, **kwargs)


class AuthTicket(typing.NamedTuple):
	ticket: str
	csrf_token: str


class InvalidSSLCertificate(Exception):
	''' Invalid certificate presented by the server '''

	def __init__(self, fingerprint:str|None=None) -> None:
		super().__init__('Invalid server SSL certificate, fingerprint: ' + str(fingerprint))


class ProxmoxApiClient:
	''' Connects to a proxmox server web API '''

	def __init__(self, url:str, user:str='', pwd:str='', fingerprint:str|None=None, timeout:int|None=None) -> None:
		self.log = logging.getLogger('pbsapi')

		parsed = urllib.parse.urlparse(url)

		if 'scheme' not in parsed:
			scheme = 'https'
		elif parsed.scheme != 'https':
			raise ValueError(f'Url "{url}" must be https')
		else:
			scheme = 'https'

		self.parsed = urllib.parse.ParseResult(
			scheme = scheme,
			netloc = parsed.netloc,
			path = parsed.path,
			params = '',
			query = '',
			fragment = ''
		)
		self.url = urllib.parse.urlunparse(self.parsed)

		self.log.debug('Base url: %s', self.url)

		self.user = user
		self.pwd = pwd

		self.fingerprint = fingerprint
		self.timeout = timeout

		self.session = requests.Session()
		if self.fingerprint:
			self.log.debug('Pinning to SSL fingerprint %s and disabling verification', self.fingerprint)
			self.session.verify = False
			self.session.mount("https://", FingerprintAdapter(self.fingerprint))

		self.ticket:AuthTicket|None = None

	def get_api2_json(self, path:str, data:dict[str,str]|None=None, auto_login:bool=True) -> typing.Any:
		if (self.user or self.pwd) and self.ticket is None and auto_login:
			res = self.get_api2_json('access/ticket', {'username':self.user, 'password':self.pwd}, auto_login=False)
			self.ticket = AuthTicket(res['ticket'], res['CSRFPreventionToken'])

		url = self.url.rstrip('/') + '/api2/json/' + path.lstrip('/')
		self.log.debug('Querying URL %s', url)

		cookies:dict[str,str] = {}
		if self.ticket:
			cookies['PVEAuthCookie'] = self.ticket.ticket

		headers:dict[str,str] = {}
		if self.ticket:
			headers['CSRFPreventionToken'] = self.ticket.csrf_token

		method = 'POST' if data else 'GET'

		req = requests.Request(method, url, cookies=cookies, data=data, headers=headers)
		prep = self.session.prepare_request(req)
		#self.log.debug('Prepared request body: %s', prep.body)

		try:
			res = self.session.send(prep, timeout=self.timeout)
		except requests.exceptions.SSLError as e:
			try:
				assert self.parsed.hostname
				fingerprint = self.get_cert_fingerprint(self.parsed.hostname, self.parsed.port)
			except Exception as e2:
				raise InvalidSSLCertificate('ERROR: ' + str(e2)) from e
			else:
				raise InvalidSSLCertificate(fingerprint) from e

		res.raise_for_status()
		decoded = res.json()
		if 'data' not in decoded:
			raise RuntimeError('Received response without data key')
		return decoded['data']

	def get_cert_fingerprint(self, host:str, port:int|None=None, hash_algo:str="sha256") -> str:
		if port is None:
			port = 80
		self.log.debug('Retrieveing fingerprint for %s:%s', host, port)
		ctx = ssl.create_default_context()
		ctx.check_hostname = False
		ctx.verify_mode = ssl.CERT_NONE

		with socket.create_connection((host, port)) as sock:
			with ctx.wrap_socket(sock, server_hostname=host) as ssock:
				der_cert = ssock.getpeercert(binary_form=True)

		if der_cert is None:
			raise RuntimeError('Empty server certificate')

		h = hashlib.new(hash_algo)
		h.update(der_cert)
		raw = h.hexdigest().upper()
		return ":".join(raw[i:i+2] for i in range(0, len(raw), 2))


class Main:
	''' The main plugin class '''

	def __init__(self, url:str, user:str='', pwd:str='', fingerprint:str|None=None, timeout:int|None=None) -> None:
		self.log = logging.getLogger('main')

		self.api = ProxmoxApiClient(url, user, pwd, fingerprint, timeout)

	def __parse_secs_interval(self, now:datetime.datetime, secs:int, name:str) -> datetime.datetime|None:
		if secs < 0:
			raise ValueError(f'{name}_secs must be >= 0')
		if secs == 0:
			return None
		return now - datetime.timedelta(seconds=secs)

	def __humanize_timedelta(self, td:datetime.timedelta) -> str:
		seconds = int(td.total_seconds())

		days = seconds // 86400
		seconds %= 86400
		hours = seconds // 3600
		seconds %= 3600
		minutes = seconds // 60

		if days > 0:
			return f"{days} days {hours} hours ago"
		if hours > 0:
			return f"{hours} hours {minutes} minutes ago"
		return f"{minutes} minutes ago"

	def run(self, warn_secs:int, crit_secs:int, include_tags:list[str]=[], exclude_tags:list[str]=[], include_vmids:list[int]=[], exclude_vmids:list[int]=[]) -> int:
		now = datetime.datetime.now().astimezone()

		warn_time = self.__parse_secs_interval(now, warn_secs, 'warn')
		crit_time = self.__parse_secs_interval(now, crit_secs, 'crit')

		# List all nodes in cluster
		node_names:list[str] = []
		for node in self.api.get_api2_json('nodes'):
			if node['status'] != 'online':
				self.log.debug('Ignoring non online node %s', node)
				continue
			node_names.append(node['node'])
		self.log.debug('Nodes: %s', node_names)

		# Last backup and VM info dict indexed by VMid
		vm_info:dict[int,dict[str,typing.Any]] = {}
		last_backups:dict[int,dict[str,typing.Any]] = {}

		# List backup storages and vms for every node
		for node_name in node_names:
			for lxc in self.api.get_api2_json(f'nodes/{node_name}/lxc'):
				assert lxc['vmid'] not in vm_info, lxc
				vm_info[lxc['vmid']] = lxc

			for qemu in self.api.get_api2_json(f'nodes/{node_name}/qemu'):
				assert qemu['vmid'] not in vm_info, qemu
				vm_info[qemu['vmid']] = qemu

			self.log.debug('VM INFO: %s', pprint.pformat(vm_info))

			storage_names:list[str] = []
			for storage in self.api.get_api2_json(f'nodes/{node_name}/storage?format=1&content=backup'):
				storage_names.append(storage['storage'])
			self.log.debug('Storages in %s: %s', node_name, storage_names)

			# List content for every storage
			for storage_name in storage_names:
				for content in self.api.get_api2_json(f'nodes/{node_name}/storage/{storage_name}/content'):
					if content['content'] != 'backup':
						continue
					if content['size'] <= 1:
						# Backups with "1" size are ongoing
						continue
					if 'verification' in content and content['verification']['state'] == 'failed':
						# Ignore backups with failed verification
						continue

					content['_ctime'] = datetime.datetime.fromtimestamp(content['ctime'], tz=datetime.timezone.utc).astimezone()

					if content['vmid'] not in last_backups or content['_ctime'] > last_backups[content['vmid']]['_ctime']:
						last_backups[content['vmid']] = content

		self.log.debug(pprint.pformat(last_backups))

		def vm_tags(info:dict) -> list[str]:
			return [t.strip() for t in (info.get('tags', '') or '').split(';')]

		# Filter vm_info based on include/exclude options.
		# include_tags and include_vmids form a union: a VM is included if it matches either.
		# exclude_tags and exclude_vmids are then applied and always take precedence.
		filtered_vm_info = {}
		for vmid, info in vm_info.items():
			if include_tags or include_vmids:
				tag_match = include_tags and any(t in vm_tags(info) for t in include_tags)
				vmid_match = include_vmids and vmid in include_vmids
				if not tag_match and not vmid_match:
					continue
			if exclude_tags and any(t in vm_tags(info) for t in exclude_tags):
				continue
			if exclude_vmids and vmid in exclude_vmids:
				continue
			filtered_vm_info[vmid] = info
		vm_info = filtered_vm_info
		last_backups = {vmid: lb for vmid, lb in last_backups.items() if vmid in vm_info}

		overall_status = OK
		perfdata:dict[str,typing.Iterable[str|int]] = {}
		details:list[str] = []
		for vmid in sorted(set(vm_info) | set(last_backups)):
			if vmid not in vm_info:
				overall_status = max(overall_status, UNKNOWN)
				details.append(f'VM {vmid} UNKNOWN: script error')
				continue

			name = vm_info[vmid]['name']

			if vmid not in last_backups:
				overall_status = max(overall_status, CRITICAL)
				details.append(f'VM {vmid}({name}) CRITICAL: no backup')
				continue

			lb = last_backups[vmid]
			if lb['_ctime'] <= crit_time:
				status_txt = 'CRITICAL'
				overall_status = max(overall_status, CRITICAL)
			elif lb['_ctime'] <= warn_time:
				status_txt = 'WARNING'
				overall_status = max(overall_status, WARNING)
			else:
				status_txt = 'OK'

			ago = now - lb['_ctime']
			ago_str = self.__humanize_timedelta(ago)
			details.append(f'VM {vmid}({name}) {status_txt}: {ago_str} at {lb['_ctime']}')
			perfdata[f'vm{vmid}'] = (str(int(ago.total_seconds()))+'s', warn_secs, crit_secs)

		self._printStatus(overall_status, perfdata, details)
		return overall_status

	def _printStatus(self, status:int, perfdata:dict[str,typing.Iterable[str|int]]={}, details:list[str]=[]):
		if status == OK:
			print('OK', end='')
		elif status == WARNING:
			print('WARNING', end='')
		elif status == CRITICAL:
			print('CRITICAL', end='')
		else:
			print('UNKNOWN', end='')

		if perfdata:
			print(' |', end='')
			for label, pds in perfdata.items():
				print(f' {label}=' + ';'.join(map(str,pds)), end='')

		print()

		for detail in details:
			print(detail)


if __name__ == '__main__':
	try:
		import argparse
		parser = argparse.ArgumentParser(
			description='Proxmox VM backup checker',
			formatter_class=argparse.ArgumentDefaultsHelpFormatter,
		)
		parser.add_argument('url', help='The base url (like https://localhost:8006/)')
		parser.add_argument('-u', '--user', default='', help='username (like nagios@pve), needs PVEAuditor and PVEDatastoreAdmin privilege')
		parser.add_argument('-p', '--pwd', default='', help='password')
		parser.add_argument('-v', '--verbose', action='store_true', help='show more output')
		parser.add_argument('--fingerprint', help='provide ssl cert fingerprint for validation')
		parser.add_argument('-w', '--warning', type=int, default=60*60*(24+4), help='warning age in seconds')
		parser.add_argument('-c', '--critical', type=int, default=60*60*(24*3+4), help='critical age in seconds')
		parser.add_argument('-t', '--timeout', type=int, default=30, help='timeout per api request in seconds')
		parser.add_argument('--include-tag', dest='include_tags', action='append', default=[], metavar='TAG', help='only check VMs with this tag (can be repeated)')
		parser.add_argument('--exclude-tag', dest='exclude_tags', action='append', default=[], metavar='TAG', help='skip VMs with this tag (can be repeated)')
		parser.add_argument('--include-vmid', dest='include_vmids', type=int, action='append', default=[], metavar='VMID', help='only check this vmid (can be repeated)')
		parser.add_argument('--exclude-vmid', dest='exclude_vmids', type=int, action='append', default=[], metavar='VMID', help='skip this vmid (can be repeated)')
		args = parser.parse_args()

		logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.WARN)
		checker = Main(args.url, args.user, args.pwd, args.fingerprint, args.timeout)
		sys.exit(checker.run(args.warning, args.critical, args.include_tags, args.exclude_tags, args.include_vmids, args.exclude_vmids))

	except InvalidSSLCertificate as e:
		print('UNKNOWN')
		print(e)
		sys.exit(UNKNOWN)

	except Exception as e:
		print('UNKNOWN')
		traceback.print_exc()
		sys.exit(UNKNOWN)

	print('UNKNOWN')
	print('Unknown error')
	sys.exit(UNKNOWN)
