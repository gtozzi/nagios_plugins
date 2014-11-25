#!/usr/bin/env python3

'''
This script is intended to be used in conjunction with procmail.

Parses an email from stdin, trying to recognize backup report emails. Stores
results into internal database to be read from check_backup_email.py

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
NAME = 'parse_backup_email'
VERSION = '0.5'
DB = 'backup_email.db'

import os, sys
import traceback
import re
import datetime
import logging
import logging.handlers
import configparser
import email.parser
import sqlite3


# Initialize logging
log = logging.getLogger(NAME)
log.setLevel(logging.DEBUG)

handler = logging.handlers.SysLogHandler(address = '/dev/log')
formatter = logging.Formatter('%(name)s: [%(levelname)s] %(message)s')
handler.setFormatter(formatter)

log.addHandler(handler)

# Read config
try:
	config = configparser.ConfigParser()
	cfgfile = os.path.splitext(os.path.abspath(__file__))[0] + '.ini'
	if not config.read(cfgfile):
		raise RuntimeError('Config file %s not found', cfgfile)

	# Reads STDIN until two consecutive empty line detected (end of email)
	parser = email.parser.FeedParser()
	empty = False
	while True:
		line = sys.stdin.readline()
		if line.strip() == '':
			if empty:
				break
			else:
				empty = True
		else:
			empty = False
		parser.feed(line)
	msg = parser.close()

	# Parse the message
	log.debug('message received, %d characters, subject: %s', len(str(msg)), msg['Subject'])

	# Try to match the message
	job = None
	for sec in config.sections():
		if sec[:4] == 'job_':
			j = sec[4:]
			matched = None
			for k, v in config.items(sec):
				if k[0] != '_':
					if k in msg and re.match(v, msg[k]):
						matched = True
					else:
						matched = False
						break
			if matched:
				log.info('Detected email for job %s', j)
				job = j
				break
	if not job:
		sys.exit(0)

	# Read job format
	fmt = config.get('job_'+job, '_format')
	matched = None
	for k, v in config.items('format_' + fmt):
		if k[0] != '_':
			if k in msg and re.match(v, msg[k]):
				matched = True
				continue
			else:
				matched = False
				break
		elif k == '_body':
			bodyMatched = None
			bodyRe = re.compile(v)
			for line in msg.get_payload().split('\n'):
				if bodyRe.match(line.strip()):
					bodyMatched = True
					break
			if bodyMatched:
				matched = True
				continue
			else:
				matched = False
				break
	if matched:
		log.info('Success status detected')
	else:
		log.info('Failure status detected')

	# Write processing result
	dbfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB)
	conn = sqlite3.connect(dbfile)
	cur = conn.cursor()
	q = """
		CREATE TABLE IF NOT EXISTS job_status (
			name TEXT,
			last TEXT,
			status INTEGER,
			PRIMARY KEY (name ASC)
		)
	"""
	cur.execute(q)

	q = "INSERT OR REPLACE INTO job_status(name, last, status) VALUES(?, ?, ?)"
	cur.execute(q, (job, datetime.datetime.now().isoformat(), int(matched)))

	conn.commit()


except Exception as e:
	log.critical('Caught exception: %s on %s', e, traceback.format_exc().splitlines()[1].strip())
	sys.exit(1)

sys.exit(0)
