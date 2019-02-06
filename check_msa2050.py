#!/usr/bin/env python3

'''
	This is an icinga / nagios plugin for reading status information from HPE MSA2050
	storage. It should work with MSA1050 too.

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
import logging
import hashlib
import datetime
import traceback
import lxml.etree
import http.client


class HpeMsaCliApiError(Exception):
	pass


class HpeMsaCliElem:
	''' Base class for property and objects '''

	def __init__(self, xml):
		self.log = logging.getLogger('msaelem')
		self.xml = xml
		self.attrs = {}
		for key in self.xml.keys():
			self.attrs[key] = self.xml.get(key)

	def __getitem__(self, key):
		return self.attrs[key]

	def __str__(self):
		return str(self.attrs)

	def __repr__(self):
		return repr(self.attrs)


class HpeMsaCliProperty(HpeMsaCliElem):
	''' Represents a property as returned by the API. '''

	def __init__(self, xml):
		super().__init__(xml)
		if self.xml.tag != 'PROPERTY':
			raise ValueError('XML root must be a property')

		if self['type'] == 'string':
			self.value = self.xml.text
		elif self['type'].startswith('uint') or self['type'].startswith('sint'):
			self.value = int(self.xml.text)
		else:
			raise NotImplementedError('Property type {} not implemented'.format(self['type']))

	def __str__(self):
		return '{} ({}): {}'.format(self['display-name'], self['name'], self.value)

	def __repr__(self):
		return repr(self.value)


class HpeMsaCliObject(HpeMsaCliElem):
	''' Represents an object as returned by the API. Objects contains properties and objects '''

	def __init__(self, xml):
		super().__init__(xml)
		if self.xml.tag != 'OBJECT':
			raise ValueError('XML root must be an object')
		self.props = {}
		for child in self.xml:
			if child.tag != 'PROPERTY':
				#TODO: Support nested objects
				self.log.info('Skipping nested object "%s"', child.tag)
				continue
			prop = HpeMsaCliProperty(child)
			self.props[prop['name']] = prop

	def __getitem__(self, key):
		return self.props[key]

	def __iter__(self):
		return iter(self.props.values())

	def __str__(self):
		return 'O<{}> {}'.format(self.attrs, self.props)


class HpeMsaCliApi:
	''' Connects to the MSA2050 Storage via the web API '''

	def __init__(self, host, user='monitor', pwd='', ssl=False, timeout=30, verifycrt=False):
		self.log = logging.getLogger('2050cli')
		self.host = host
		self.user = user
		self.pwd = pwd
		self.ssl = ssl
		self.timeout = 30
		self.verifycrt = verifycrt

		self.conn = None
		self.token = None

	def connect(self):
		if self.ssl:
			if self.verifycrt:
				self.log.debug('Initing HTTPS verified connection')
			else:
				self.log.debug('Initing HTTPS unverified connection')
				unsafectx = ssl._create_unverified_context()
			self.conn = http.client.HTTPSConnection(self.host, timeout=self.timeout, context=unsafectx)
		else:
			self.log.debug('Initing HTTP connection')
			self.conn = http.client.HTTPConnection(self.host, timeout=self.timeout)

	def request(self, path):
		''' Execute the API request
		@param path string: The path, without the /api/ part
		@return list of HpeMsaCliObject
		'''
		if not self.conn:
			self.connect()

		headers = {
			'User-Agent': 'check_msa2050.py',
			'dataType': 'ipa',
		}
		if self.token:
			headers['sessionKey'] = self.token

		path = '/api/' + path
		self.log.debug('Requesting %s, h:%s', path, headers)
		self.conn.request('GET', path, headers=headers)
		res = self.conn.getresponse()
		data = res.read()
		if res.status != 200:
			self.log.error('%s status received', res.status)
			self.log.error(data)
			raise HpeMsaCliApiError('{} status received'.format(res.status))

		try:
			root = lxml.etree.fromstring(data)
		except:
			self.log.error('Error parsing XML object')
			self.log.error(data)
			raise HpeMsaCliApiError('Error parsing XML object')

		self.log.debug(lxml.etree.tostring(root, encoding='unicode', pretty_print=True))
		if root.tag != 'RESPONSE':
			self.log.error('Response node is not root')
			raise HpeMsaCliApiError('Response node is not root')

		res = []
		for child in root:
			obj = HpeMsaCliObject(child)
			res.append(obj)

		self.log.debug(res)
		return res

	def login(self):
		''' Does initial login '''
		self.log.debug('Logging in')
		hash = hashlib.sha256()
		hash.update(self.user.encode())
		hash.update(b'_')
		hash.update(self.pwd.encode())

		path = 'login/' + hash.hexdigest()
		res = self.request(path)
		if res[0]['response-type-numeric'].value != 0:
			raise HpeMsaCliApiError('Authentication unsuccesful')

		self.token = res[0]['response'].value
		self.log.debug('Logged in with token {}'.format(self.token))

	def cmd(self, cmd):
		''' Executes a command, connect and login automatically when needed
		@param cmd iterable: The command (es. ['show', 'disk'])
		'''
		if not self.token:
			self.login()

		self.log.debug('Executing command "{}"'.format(' '.join(cmd)))
		path = '/'.join(cmd)
		return self.request(path)


class Main:
	''' The main plugin class '''

	OK = 0
	WARNING = 1
	CRITICAL = 2
	UNKNOWN = 3

	TEMPMAX = 40
	SPACE_WARN_PCT = 85
	SPACE_CRIT_PCT = 95

	def __init__(self, host, user='monitor', pwd='', ssl=False):
		self.log = logging.getLogger('main')
		self.host = host
		self.user = user
		self.pwd = pwd
		self.ssl = ssl
		self.cli = HpeMsaCliApi(self.host, self.user, self.pwd, self.ssl)

	def run(self, check):
		if not check.isalpha():
			raise ValueError('Check "{}" unknown'.format(check))
		if not hasattr(self, check):
			raise ValueError('Check "{}" unknown'.format(check))
		method = getattr(self, check)
		if not callable(method):
			raise ValueError('Check "{}" unknown'.format(check))

		return method()

	def _printStatus(self, status, summary=None):
		if status == self.OK:
			print('OK', end='')
		elif status == self.WARNING:
			print('WARNING', end='')
		elif status == self.CRITICAL:
			print('CRITICAL', end='')
		else:
			print('UNKNOWN', end='')

		if summary:
			print(': ', end='')
			print(summary, end='')
		print()

	def _check(self, cmd):
		''' Base check function '''
		status = self.OK
		res = self.cli.cmd(cmd)
		message = []

		for obj in res:
			if obj.attrs['basetype'] == 'status':
				if obj['return-code'].value != 0:
					message.append('Non-zero status: {}'.format(obj['return-code']))
					status = max(status, self.WARNING)

		return res, status, message

	def _ret(self, status, message=None, summary=None):
		''' Base return function '''
		self._printStatus(status, summary)
		if message:
			print(', '.join(message))
		return status

	def disks(self):
		res, status, message = self._check(('show', 'disks'))

		summaries = {}
		count = 0

		for obj in res:
			if obj.attrs['basetype'] == 'drives':
				count += 1
				name = obj['durable-id'].value
				health = obj['health'].value
				message.append('{}: {}'.format(name, health))
				if health != 'OK':
					status = self.CRITICAL

				if health not in summaries:
					summaries[health] = 0
				summaries[health] += 1

		summary = ', '.join('{}/{} {}'.format(v,count,k) for k,v in summaries.items()) 
		return self._ret(status, message, summary)

	def diskstemp(self):
		res, status, message = self._check(('show', 'disks'))

		summaries = {}
		count = 0

		for obj in res:
			if obj.attrs['basetype'] == 'drives':
				count += 1
				name = obj['durable-id'].value
				temp = obj['temperature-numeric'].value
				tempstatus = obj['temperature-status'].value
				message.append('{}: {} {}°C'.format(name, tempstatus, temp))
				if tempstatus != 'OK':
					status = self.CRITICAL
				elif temp > self.TEMPMAX:
					status = max(status, self.CRITICAL)

				if tempstatus not in summaries:
					summaries[tempstatus] = 0
				summaries[tempstatus] += 1

		summary = ', '.join('{}/{} {}'.format(v,count,k) for k,v in summaries.items()) 
		return self._ret(status, message, summary)

	def volumes(self):
		res, status, message = self._check(('show', 'volumes'))

		for obj in res:
			if obj.attrs['basetype'] == 'volumes':
				name = obj['durable-id'].value
				health = obj['health'].value
				total = obj['total-size-numeric'].value
				totalstr = obj['total-size'].value
				allocated = obj['allocated-size-numeric'].value
				allocatedstr = obj['allocated-size'].value
				used = allocated / total

				space = ''
				if used * 100 > self.SPACE_CRIT_PCT:
					status = self.CRITICAL
					space = '!'
				elif used * 100 > self.SPACE_WARN_PCT:
					status = max(status, self.WARNING)
					space = '!'

				message.append(r'{} {} {}/{} ({:.1%}{})'.format(name, health, allocatedstr, totalstr, used, space))
				if health != 'OK':
					status = self.CRITICAL

		return self._ret(status, None, ', '.join(message))

	def system(self):
		res, status, message = self._check(('show', 'system'))

		for obj in res:
			if obj.attrs['basetype'] == 'system':
				name = obj['system-name'].value
				health = obj['health'].value

				message.append(r'{} {}'.format(name, health))
				if health != 'OK':
					status = self.CRITICAL

		return self._ret(status, None, ', '.join(message))

	def events(self):
		res, status, message = self._check(('show', 'events', 'error'))

		severities = {}
		for obj in res:
			if obj.attrs['basetype'] == 'events':
				count += 1
				severity = obj['severity'].value
				if severity not in severities:
					severities[severity] = 0
				severities[severity] += 1

				if severity in ('CRITICAL', 'ERROR'):
					status = self.CRITICAL
				elif severity == 'WARNING':
					status = max(status, self.WARNING)

		if not severities:
			message.append('no events')
		for k, v in severities.items(): 
			message.append('{} {}'.format(v, k))

		return self._ret(status, None, ', '.join(message))


if __name__ == '__main__':
	try:
		import argparse
		cmds = ('disks', 'diskstemp', 'volumes', 'system', 'events')
		parser = argparse.ArgumentParser(description='MSA2050 nagios plugin')
		parser.add_argument('host', help='The hostname or IP address')
		parser.add_argument('check', choices=cmds, help='What to check')
		parser.add_argument('-u', '--user', default='monitor', help='username')
		parser.add_argument('-p', '--pwd', default='', help='password')
		parser.add_argument('-s', '--https', action='store_true', help='use HTTPS')
		parser.add_argument('-v', '--verbose', action='store_true', help='show more output')
		args = parser.parse_args()

		logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.WARN)
		checker = Main(args.host, args.user, args.pwd, args.https)
		sys.exit(checker.run(args.check))

	except Exception as e:
		print('UNKNOWN')
		traceback.print_exc()
		sys.exit(Main.UNKNOWN)

	print('UNKNOWN')
	print('Unknown error')
	sys.exit(Main.UNKNOWN)
