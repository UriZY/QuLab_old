# -*- coding: utf-8 -*-
import copy
import importlib
import logging
import os
import re
import string

import numpy as np
import quantities as pq
# import visa
import pyvisa as visa

from .util import IEEE_488_2_BinBlock, get_unit_prefix

log = logging.getLogger('qulab.driver')
log.addHandler(logging.NullHandler())

__all__ = [
    'QReal', 'QInteger', 'QString', 'QOption', 'QBool', 'QVector', 'QList',
    'BaseDriver', 'DriverManager'
]


class Quantity:
    def __init__(self,
                 name,
                 value=None,
                 type=None,
                 unit=None,
                 ch=None,
                 get_cmd='',
                 set_cmd=''):
        self.name = name
        self.value = value
        self.type = type
        self.unit = unit
        self.ch = ch
        self.driver = None
        self.set_cmd = set_cmd
        self.get_cmd = get_cmd

    def __str__(self):
        return '%s' % self.value

    def setDriver(self, driver):
        self.driver = driver

    def getValue(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        return self.value

    def setValue(self, value, unit=None, ch=None, **kw):
        self.value = value
        if ch is None:
            ch = self.ch
        if unit is None:
            unit = self.unit
        if self.driver is not None and self.set_cmd is not '':
            cmd = self._formatSetCmd(value, unit=unit, ch=ch, **kw)
            self.driver.write(cmd)

    def _formatGetCmd(self, **kw):
        return self.get_cmd % dict(**kw)

    def _formatSetCmd(self, value, **kw):
        return self.set_cmd % dict(value=value, **kw)


class QReal(Quantity):
    def __init__(self,
                 name,
                 value=None,
                 unit=None,
                 ch=None,
                 get_cmd='',
                 set_cmd=''):
        super(QReal, self).__init__(
            name, value, 'Real', unit, ch, get_cmd=get_cmd, set_cmd=set_cmd)

    def __str__(self):
        p, r = get_unit_prefix(self.value)
        value = self.value / r
        unit = p + self.unit
        return '%g %s' % (value, unit)

    def getValue(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        if self.driver is not None and self.get_cmd is not '':
            cmd = self._formatGetCmd(ch=ch, **kw)
            res = self.driver.query_ascii_values(cmd)
            self.value = res[0]
        return self.value


class QInteger(QReal):
    def __init__(self,
                 name,
                 value=None,
                 unit=None,
                 ch=None,
                 get_cmd='',
                 set_cmd=''):
        Quantity.__init__(
            self,
            name,
            value,
            'Integer',
            unit,
            ch,
            get_cmd=get_cmd,
            set_cmd=set_cmd)

    def getValue(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        super(QInteger, self).getValue(ch=ch, **kw)
        return int(self.value)


class QString(Quantity):
    def __init__(self, name, value=None, ch=None, get_cmd='', set_cmd=''):
        super(QString, self).__init__(
            name, value, 'String', ch=ch, get_cmd=get_cmd, set_cmd=set_cmd)

    def getValue(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        if self.driver is not None and self.get_cmd is not '':
            cmd = self._formatGetCmd(ch=ch, **kw)
            res = self.driver.query(cmd)
            self.value = res.strip("\n\"' ")
        return self.value


class QOption(QString):
    def __init__(self,
                 name,
                 value=None,
                 options=[],
                 ch=None,
                 get_cmd='',
                 set_cmd=''):
        Quantity.__init__(
            self,
            name,
            value,
            'Option',
            ch=ch,
            get_cmd=get_cmd,
            set_cmd=set_cmd)
        self.options = options
        self._opts = {}
        for k, v in self.options:
            self._opts[k] = v
            self._opts[v] = k

    def setValue(self, value, ch=None, **kw):
        self.value = value
        if ch is None:
            ch = self.ch
        if self.driver is not None and self.set_cmd is not '':
            options = dict(self.options)
            if value not in options.keys():
                #logger.error('%s not in %s options' % (value, self.name))
                return
            cmd = self.set_cmd % dict(option=options[value], ch=ch, **kw)
            self.driver.write(cmd)

    def getIndex(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        value = self.getValue(ch=ch, **kw)
        if value is None:
            return None

        for i, pair in enumerate(self.options):
            if pair[0] == value:
                return i
        return None

    def getCmdOption(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        value = self.getValue(ch=ch, **kw)
        if value is None:
            return None
        return dict(self.options)[value]


class QBool(QInteger):
    def __init__(self, name, value=None, ch=None, get_cmd='', set_cmd=''):
        Quantity.__init__(
            self, name, value, 'Bool', ch=ch, get_cmd=get_cmd, set_cmd=set_cmd)

    def getValue(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        return bool(super(QBool, self).getValue(ch=ch, **kw))


class QVector(Quantity):
    def __init__(self,
                 name,
                 value=None,
                 unit=None,
                 ch=None,
                 get_cmd='',
                 set_cmd=''):
        super(QVector, self).__init__(
            name, value, 'Vector', unit, ch, get_cmd=get_cmd, set_cmd=set_cmd)

    def getValue(self, ch=None, **kw):
        if ch is None:
            ch = self.ch
        if self.driver is not None and self.get_cmd is not '':
            cmd = self._formatGetCmd(ch=ch, **kw)
            if kw.get('binary'):
                res = self.driver.query_binary_values(cmd)
            else:
                res = self.driver.query_ascii_values(cmd)
            self.value = np.asarray(res)
        return self.value


class QList(Quantity):
    def __init__(self,
                 name,
                 value=None,
                 unit=None,
                 ch=None,
                 get_cmd='',
                 set_cmd=''):
        super(QList, self).__init__(
            name, value, 'List', unit, ch, get_cmd=get_cmd, set_cmd=set_cmd)


class BaseDriver:
    """Base class for Driver"""

    error_command = 'SYST:ERR?'
    """The SCPI command to query errors."""

    support_models = []
    """"""

    quants = []
    """"""

    def __init__(self, ins=None, addr=None, model=None, timeout=3, **kw):
        self.addr = addr
        self.ins = ins
        self.timeout = timeout
        if self.ins is not None:
            self.ins.timeout = timeout * 1000
        self.quantities = {}
        self.model = model

        for q in self.quants:
            self._add_quant(q)

    def __del__(self):
        self.close()

    def __repr__(self):
        return 'Driver(addr=%s)' % self.addr

    def _add_quant(self, quant):
        self.quantities[quant.name] = copy.deepcopy(quant)
        self.quantities[quant.name].driver = self

    def set_timeout(self, t):
        self.timeout = t
        if self.ins is not None:
            self.ins.timeout = t * 1000
        return self

    def errors(self):
        """返回错误列表"""
        e = []
        if self.error_command == '':
            return e
        while True:
            s = self.ins.query(self.error_command)
            _ = s[:-1].split(',"')
            code = int(_[0])
            msg = _[1]
            if code == 0:
                break
            e.append((code, msg))
        return e

    def check_errors_and_log(self, message):
        errs = self.errors()
        for e in errs:
            log.error("%s << %s", str(self.ins), message)
            log.error("%s >> %s", str(self.ins), ("%d : %s" % e))

    def query(self, message, check_errors=False):
        if self.ins is None:
            return None
        log.debug("%s << %s", str(self.ins), message)
        try:
            res = self.ins.query(message)
        except:
            log.exception("%s << %s", str(self.ins), message)
            raise
        log.debug("%s >> %s", str(self.ins), res)
        if check_errors:
            self.check_errors_and_log(message)
        return res

    def query_ascii_values(self,
                           message,
                           converter='f',
                           separator=',',
                           container=list,
                           delay=None,
                           check_errors=False):
        if self.ins is None:
            return None
        log.debug("%s << %s", str(self.ins), message)
        try:
            res = self.ins.query_ascii_values(message, converter, separator,
                                              container, delay)
        except:
            log.exception("%s << %s", str(self.ins), message)
            raise
        log.debug("%s >> <%d results>", str(self.ins), len(res))
        if check_errors:
            self.check_errors_and_log(message)
        return res

    def query_binary_values(self,
                            message,
                            datatype='f',
                            is_big_endian=False,
                            container=list,
                            delay=None,
                            header_fmt='ieee',
                            check_errors=False):
        if self.ins is None:
            return None
        log.debug("%s << %s", str(self.ins), message)
        try:
            res = self.ins.query_binary_values(
                message, datatype, is_big_endian, container, delay, header_fmt)
        except:
            log.exception("%s << %s", str(self.ins), message)
            raise
        log.debug("%s >> <%d results>", str(self.ins), len(res))
        if check_errors:
            self.check_errors_and_log(message)
        return res

    def write(self, message, check_errors=False):
        """Send message to the instrument."""
        if self.ins is None:
            return None
        log.debug("%s << %s", str(self.ins), message)
        try:
            ret = self.ins.write(message)
        except:
            log.exception("%s << %s", str(self.ins), message)
            raise
        if check_errors:
            self.check_errors_and_log(message)
        return self

    def write_ascii_values(self,
                           message,
                           values,
                           converter='f',
                           separator=',',
                           termination=None,
                           encoding=None,
                           check_errors=False):
        if self.ins is None:
            return None
        log_msg = message + ('<%d values>' % len(values))
        log.debug("%s << %s", str(self.ins), log_msg)
        try:
            ret = self.ins.write_ascii_values(message, values, converter,
                                              separator, termination, encoding)
        except:
            log.exception("%s << %s", str(self.ins), log_msg)
            raise
        if check_errors:
            self.check_errors_and_log(log_msg)
        return self

    def write_binary_values(self,
                            message,
                            values,
                            datatype='f',
                            is_big_endian=False,
                            termination=None,
                            encoding=None,
                            check_errors=False):
        if self.ins is None:
            return None
        block, header = IEEE_488_2_BinBlock(values, datatype, is_big_endian)
        log_msg = message + header + '<DATABLOCK>'
        log.debug("%s << %s", str(self.ins), log_msg)
        try:
            ret = self.ins.write_binary_values(message, values, datatype,
                                               is_big_endian, termination,
                                               encoding)
        except:
            log.exception("%s << %s", str(self.ins), log_msg)
            raise
        if check_errors:
            self.check_errors_and_log(log_msg)
        return self

    def getValue(self, name, **kw):
        if name in self.quantities:
            return self.performGetValue(self.quantities[name], **kw)
        else:
            return None

    def getIndex(self, name, **kw):
        if name in self.quantities:
            return self.quantities[name].getIndex(**kw)

    def getCmdOption(self, name, **kw):
        if name in self.quantities:
            return self.quantities[name].getCmdOption(**kw)

    def setValue(self, name, value, **kw):
        if name in self.quantities:
            self.performSetValue(self.quantities[name], value, **kw)
        return self

    def performOpen(self, **kw):
        pass

    def performClose(self, **kw):
        pass

    def performGetValue(self, quant, **kw):
        return quant.getValue(**kw)

    def performSetValue(self, quant, value, **kw):
        quant.setValue(value, **kw)

    def init(self, cfg={}):
        log.debug('Init instr ...')
        for key in cfg.keys():
            if isinstance(cfg[key], dict):
                self.setValue(key, **cfg[key])
            else:
                self.setValue(key, cfg[key])
        log.debug('Init instr ... Done')
        return self

    def close(self):
        self.performClose()
        if self.ins is not None:
            self.ins.close()


def _load_driver(driver_name):
    log.debug('Loading driver %s ...' % driver_name)
    mod = importlib.import_module(driver_name)
    return getattr(mod, 'Driver')


ats_addr = re.compile(
    r'^(ATS9360|ATS9850|ATS9870)::SYSTEM([0-9]+)::([0-9]+)(|::INSTR)$')
gpib_addr = re.compile(r'^GPIB[0-9]?::[0-9]+(::.+)？$')
p_addr = re.compile(r'^([a-zA-Z]+)[0-9]*::.+$')


def parse_resource_name(addr):
    m = p_addr.search(addr)
    protocol = m.group(1).upper()
    return protocol, addr


def _parse_ats_resource_name(m, addr):
    model = m.group(1)
    systemID = int(m.group(2))
    boardID = int(m.group(3))
    return dict(
        type='ATS',
        ins=None,
        company='AlazarTech',
        model=model,
        systemID=systemID,
        boardID=boardID,
        addr=addr)


def _parse_resource_name(addr):
    m = ats_addr.search(addr)
    if m is not None:
        return _parse_ats_resource_name(m, addr)
    else:
        return dict(type='Visa', addr=addr)


def _open_visa_resource(rm, addr):
    ins = rm.open_resource(addr)
    IDN = ins.query("*IDN?").split(',')
    company = IDN[0].strip()
    model = IDN[1].strip()
    version = IDN[3].strip()
    return dict(
        ins=ins, company=company, model=model, version=version, addr=addr)


class DriverManager(object):
    def __init__(self, visa_backends='@ni'):
        self.__drivers = []
        self.__instr = {}
        self.rm = visa.ResourceManager(visa_backends)

    def __del__(self):
        for ins in self.__instr.values():
            ins.close()

    def __getitem__(self, key):
        return self.get(key)

    def get(self, key):
        return self.__instr.get(key, None)

    def _open_resource(self, addr, driver_name, **kw):
        info = _parse_resource_name(addr)
        if info['type'] == 'Visa':
            info = _open_visa_resource(self.rm, addr)
        Driver = _load_driver(driver_name)
        info.update(kw)
        ins = Driver(**info)
        ins.performOpen()
        return ins

    def open(self, instrument, **kw):
        if isinstance(instrument, str):
            instrument = db.query.getInstrumentByName(instrument)
        if instrument.name not in self.__instr.keys():
            self.__instr[instrument.name] = self._open_resource(
                instrument.address, instrument.driver, **kw)
        return self.__instr[instrument.name]
