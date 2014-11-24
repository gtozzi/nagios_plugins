#!/usr/bin/env python
# -*- coding: utf-8 -*-
# kate: space-indent on; mixedindent off;

'''
    This is a nagios plugin that connects via telnet to an intermediate host to
    send a ping to the host to be checked
    
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
NAME = 'ping_via_telnet'
VERSION = '1.0'

import re
import argparse
import os, sys
import traceback
import telnetlib


class Ret(object):
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
            + ( "\n"+self.__detail if self.__detail is not None else '')
    
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


class MyTelnet(telnetlib.Telnet):
    
    PORT = 23
    TIMEOUT = 30
    
    def __init__(self, host):
        return telnetlib.Telnet.__init__(self, host, self.PORT, self.TIMEOUT)
    
    def chat(self, expected, message):
        """ Send message to the telnet client after expected message has been received """
        res = self.read_until(expected, self.TIMEOUT)
        if not expected in res:
            raise RuntimeError('Unexpected answer: %s' % res)
        self.write(message)


class Main(object):
    
    def __init__(self, host, inter, user, pasw):
        self.host = host
        self.inter = inter
        self.user = user
        self.pasw = pasw
        self.ret = Ret()
    
    def runCheck(self):
        conn = MyTelnet(self.inter)
        
        conn.chat('login: ', self.user + "\n")
        conn.chat('Password: ', self.pasw + "\n")
        conn.chat('# ', 'ping -c 4 -q ' + self.host + "\n")
        
        lines = conn.read_until('# ')
        
        conn.close()
        
        resre = re.compile(r'^([0-9]+) packets transmitted, ([0-9]+) packets received, ([0-9]+)% packet loss$')
        for l in lines.split("\n"):
            m = resre.match(l.strip())
            if m:
                loss = int(m.group(3))
                if loss == 100:
                    self.ret.change(Ret.CRITICAL, "%d%% packet loss" % loss)
                elif loss > 0:
                    self.ret.change(Ret.WARNING, "%d%% packet loss" % loss)
                else:
                    self.ret.change(Ret.OK, "%d%% packet loss" % loss)
                break
        else:
            self.ret.change(Ret.UNKNOWN, "PING answer not found" % loss, lines)
        
        return self.ret


if __name__ == '__main__':
    ret = None
    try:
        parser = argparse.ArgumentParser(description=NAME+' '+VERSION+': '+__doc__, prog=NAME)
        parser.add_argument('host', help='the address of the host to ping')
        parser.add_argument('intermediate', help='the address of the host to telnet into')
        parser.add_argument('user', help='telnet username')
        parser.add_argument('pwd', help='telnet password')
        
        args = parser.parse_args()
        
        checker = Main(args.host, args.intermediate, args.user, args.pwd)
        ret = checker.runCheck()
        
    except Exception as e:
        ret = Ret()
        ret.change(Ret.UNKNOWN, 'Plugin exception: ' + str(e),
            str(e.__class__) + "\n" + traceback.format_exc())
    else:
        if not ret:
            ret = Ret()
    print(ret.getText())
    sys.exit(ret.getCode())
