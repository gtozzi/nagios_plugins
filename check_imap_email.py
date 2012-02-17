#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
    This is a nagios plugin wich goal is to read an IMAP mail folder and check
    of last email with given characteristics in it.
    
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
    @author: Andrea De Angeli <andrea.deangeli@deaconsulenze.eu>
    
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
NAME = 'check_imap_email'
VERSION = '0.1'

import sys, traceback
import argparse
from xml.etree import ElementTree
import re

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
        elif CODES.index(code) > CODES.index(self.__code):
            self.__code = code
            self.__message = str(message)
            self.__detail = str(detail)
        else:
            pass
    
class Config:
    ''' Parsed representation of configuration '''
    
    def __init__(self, filename):
        self.__xml = ElementTree.parse(filename)
    
    def getTemplate(self, name):
        ''' Returns a template by ID '''
        return self.Template(self.__findNodeOfTypeById('template',name))
    
    def getFolder(self, name):
        ''' Returns a folder by ID '''
        return self.Folder(self.__findNodeOfTypeById('folder',name))
    
    def __findNodeOfTypeById(self, nType, nId):
        node = None
        for n in self.__xml.findall(nType):
            if n.get('id') == nId:
                node = n
                break
        if node == None:
            raise self.ElementNotFoundError(str(nType).capitalize() + ' ' + str(nId) + ' not found!')
        return node
    
    class Folder:
        def __init__(self, node):
            self.server = node.get('server')
            self.user = node.get('user')
            self.pwd = node.get('pass')
    
    class Template:
        
        def __init__(self, node):
            self.__subjects = []
            self.__bodies = []
            for s in node.findall('subject'):
                self.__subjects.append(self.Subject(s))
            for s in node.findall('body'):
                self.__bodies.append(self.Body(s))
        
        class MailPart:
            def __init__(self, node):
                self.re = re.compile(node.text,re.M)
                self.status = node.get('status')
        
        class Subject(MailPart):
            pass
        
        class Body(MailPart):
            pass
    
    class ElementNotFoundError(RuntimeError):
        pass
        
class Main:
    
    def __init__(self):
        ''' Read config and command line '''

        parser = argparse.ArgumentParser(description=NAME+' '+VERSION+': '+__doc__, prog=NAME)
        parser.add_argument('folder',
            help='the folder to look in')
        parser.add_argument('template',
            help='the template to look for')

        self.__args = parser.parse_args()
        self.__config = Config(NAME+'.xml')
    
    def run(self):
        ''' Run the checks '''
        
        # Load the template
        template = self.__config.getTemplate(self.__args.template)
        
        # Open the folder
        folder = self.__config.getFolder(self.__args.folder)

if __name__ == '__main__':
    try:
        ret = Main().run()
    except Config.ElementNotFoundError as e:
        ret = Ret()
        ret.change(Ret.UNKNOWN, 'Config error: ' + str(e))
    except Exception as e:
        ret = Ret()
        ret.change(Ret.UNKNOWN, 'Plugin exception: ' + str(e), traceback.format_exc())
    else:
        if not ret:
            ret = Ret()
    print ret.getText()
    sys.exit(ret.getCode())
