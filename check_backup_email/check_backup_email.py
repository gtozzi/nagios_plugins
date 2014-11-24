#!/usr/bin/env python3

'''
Reads status from database create by parse_backup_email and returns it

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
NAME = 'check_backup_email'
VERSION = '0.5'
DB = 'backup_email.db'

import sys, os
import traceback
import datetime
import argparse
import sqlite3


class Ret:
	''' Return code and text handler '''
	OK = 0
	WARNING = 1
	CRITICAL = 2
	UNKNOWN = 3
	
	CODES = (UNKNOWN, OK, WARNING, CRITICAL)
	DESCR = ('OK', 'WARNING', 'CRITICAL', 'UNKNOWN')

	def __init__(self):
		self.__code = self.UNKNOWN
		self.__message = None
		self.__detail = None
	
	def getCode(self):
		''' Returns numeric exit code '''
		return self.__code
	
	def getText(self):
		''' Returns textual message '''
		return self.DESCR[self.__code] + ( ': '+self.__message if self.__message!=None else '') \
			+ ( "\n"+self.__detail if self.__detail!=None else '')
	
	def change(self, code, message=None, detail=None):
		'''
			Sets new code and message. If the new condition is not worsen than,
			previous, then old condition is kept. Is condition is the same, then
			message is concatenated
		'''
		if not code in self.CODES:
			raise RuntimeError('Unvalid code: ' + str(code))
		if code == self.__code:
			if message != None:
				if self.__message == None:
					self.__message = message
				else:
					self.__message += '; ' + message
			if detail != None:
				if self.__detail == None:
					self.__detail = detail
				else:
					self.__detail += "\n----------\n" + detail
		elif self.CODES.index(code) > self.CODES.index(self.__code):
			self.__code = code
			self.__message = str(message)
			self.__detail = str(detail)
		else:
			pass


class Main:

	def __init__(self, job, warn, crit):
		''' Open database and perform init tasks'''
		self.job = job
		self.warn = warn
		self.crit = crit

		dbfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB)
		self.conn = sqlite3.connect(dbfile)

	def run(self):
		''' Run the check '''
		ret = Ret()
		cur = self.conn.cursor()

		q = "SELECT last, status FROM job_status WHERE name = ?"
		cur.execute(q, (self.job,))
		res = cur.fetchone()
		if not res:
			ret.change(Ret.UNKNOWN, "Job {} has never been recorded".format(self.job))
			return ret
		time = datetime.datetime.strptime(res[0], r'%Y-%m-%dT%H:%M:%S.%f')
		status = bool(res[1])
		elapsed = datetime.datetime.now() - time
		mex = "Job {} recorded on {:%d-%m-%Y at %H:%M:%S}".format(self.job, time)

		if status:
			ret.change(Ret.OK, mex)

		if elapsed.total_seconds() > 60 * 60 * self.crit:
			ret.change(Ret.CRITICAL, mex)
		elif elapsed.total_seconds() > 60 * 60 * self.warn:
			ret.change(Ret.WARNING, mex)

		return ret


if __name__ == '__main__':
	parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument("job", help="name of the backup job")
	parser.add_argument("-w", "--warn", type=int, default=25, help="warning time in hours")
	parser.add_argument("-c", "--crit", type=int, default=48, help="critical time in hours")
	args = parser.parse_args()

	try:
		ret = Main(args.job, args.warn, args.crit).run()
	except Exception as e:
		ret = Ret()
		ret.change(Ret.UNKNOWN, 'Plugin exception: ' + str(e),
			str(e.__class__) + "\n" + traceback.format_exc())
	else:
		if not ret:
			ret = Ret()

	print(ret.getText())
	sys.exit(ret.getCode())
