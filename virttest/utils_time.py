import logging
import re

from avocado.utils import process
from avocado.core import exceptions

from virttest import error_context
from virttest import utils_test
from virttest.compat_52lts import decode_to_text


@error_context.context_aware
def get_host_timezone():
    """
    Get host's timezone
    """
    timezone_cmd = 'timedatectl | grep "Time zone"'
    timezone_pattern = '^(?:\s+Time zone:\s)(\w+\/\S+|UTC)(?:\s\(\S+,\s)([+|-]\d{4})\)$'
    error_context.context("Get host's timezone", logging.info)
    host_timezone = decode_to_text(process.system_output(timezone_cmd, timeout=240, shell=True))
    try:
        host_timezone_set = re.match(timezone_pattern, host_timezone).groups()
        return {"timezone_city": host_timezone_set[0],
                "timezone_code": host_timezone_set[1]}
    except (AttributeError, IndexError):
        raise exceptions.TestError("Fail to get host's timezone.")


@error_context.context_aware
def verify_timezone_linux(session):
    """
    Verify linux guest's timezone

    :param session: VM session
    """
    error_context.context("Verify guest's timezone", logging.info)
    timezone_cmd = 'timedatectl | grep "Time zone"'
    timezone_pattern = '^(?:\s+Time zone:\s)(\w+\/\S+|UTC)(?:\s\(\S+,\s)([+|-]\d{4})\)$'
    guest_timezone = session.cmd_output(timezone_cmd, timeout=240)
    try:
        guest_timezone_set = re.match(timezone_pattern, guest_timezone).groups()
        return (guest_timezone_set[0] == get_host_timezone()['timezone_city'])
    except (AttributeError, IndexError):
        raise exceptions.TestError("Fail to get guest's timezone.")


@error_context.context_aware
def sync_timezone_linux(session):
    """
    Sync linux guest's timezone

    :param session: VM session
    """
    error_context.context("Sync guest's timezone", logging.info)
    set_timezone_cmd = "timedatectl set-timezone %s"
    if not verify_timezone_linux(session):
        host_timezone_city = get_host_timezone()['timezone_city']
        session.cmd(set_timezone_cmd % host_timezone_city)
        if not verify_timezone_linux(session):
            raise exceptions.TestError("Fail to sync guest's timezone.")


@error_context.context_aware
def verify_timezone_win(session):
    """
    Verify windows guest's timezone

    :params session: VM session
    :return tuple(verify_status, get_timezone_name)
    """
    def get_timezone_list():
        timezone_list_cmd = "tzutil /l"
        timezone_set = []
        timezone_sets = []
        timezone_list = session.cmd_output(timezone_list_cmd)

        for line in timezone_list.splitlines():
            # Empty line
            if not line:
                continue
            _code = re.search(r'(?:\(UTC([+|-]\d{2}:\d{2})?)', line)
            if _code and _code.group(1):
                _code_ = re.sub(r'(\d{2}):(\d{2})', r'\1\2',
                                _code.group(1))
                timezone_set.append(_code_)
                continue
            # When UTC standard time, add timezone code '+0000'
            elif _code:
                timezone_set.append("+0000")
                continue
            _name = re.search(r'(\S+(?:\s\S+)*)', line)
            if _name:
                timezone_set.append(_name.group(0))
            else:
                logging.warn("Can not get timezone name correctly.")
            timezone_sets.append(timezone_set)
            timezone_set = []
        return timezone_sets

    def get_timezone_code(timezone_name):
        for value in get_timezone_list():
            if value[1] == timezone_name:
                return value[0]
        return None

    def get_timezone_name(timezone_code):
        for value in get_timezone_list():
            if value[0] == timezone_code:
                return value[1]
        return None

    error_context.context("Verify guest's timezone", logging.info)
    timezone_cmd = 'tzutil /g'
    host_timezone_code = get_host_timezone()['timezone_code']
    timezone_name = session.cmd_output(timezone_cmd).strip('\n')
    if get_timezone_code(timezone_name) != host_timezone_code:
        return False, get_timezone_name(host_timezone_code)
    return True, ""


@error_context.context_aware
def sync_timezone_win(vm):
    """
    Verify and sync windows guest's timezone

    :param vm: Virtual machine for vm
    """
    session = vm.wait_for_login()
    set_timezone_cmd = 'tzutil /s "%s"'
    (ver_result, output) = verify_timezone_win(session)

    if ver_result is not True:
        error_context.context("Sync guest's timezone.", logging.info)
        session.cmd(set_timezone_cmd % output)
        vm_params = vm.params
        error_context.context("Shutdown guest...", logging.info)
        vm.destroy()
        error_context.context("Boot guest...", logging.info)
        vm.create(params=vm_params)
        vm.verify_alive()
        session = vm.wait_for_login()
        (ver_result, output) = verify_timezone_win(session)
        if ver_result is not True:
            session.close()
            raise exceptions.TestError("Fail to sync guest's timezone.")
    session.close()


def execute(cmd, timeout=360, session=None):
    """
    Execute command in guest or host, if session is not None return
    command output in guest else return command ouput in host

    :param cmd: Shell commands
    :param timeout: Timeout to execute command
    :param session: ShellSession or None

    :return: Command output string
    """
    if session:
        ret = session.cmd_output(cmd, timeout=timeout)
    else:
        ret = process.getoutput(cmd)
    target = session and "guest" or "host"
    logging.debug("(%s) Execute command('%s')" % (target, cmd))
    return ret


@error_context.context_aware
def verify_clocksource(expected, session=None):
    """
    Verify if host/guest use the expected clocksource
    :param expected: Expected clocksource
    :param session: VM session
    """
    error_context.context("Check the current clocksource", logging.info)
    cmd = "cat /sys/devices/system/clocksource/"
    cmd += "clocksource0/current_clocksource"
    return expected in execute(cmd, session=session)


@error_context.context_aware
def sync_time_with_ntp(session=None):
    """
    Sync guest or host time with ntp server
    :param session: VM session or None
    """
    error_context.context("Sync time from ntp server", logging.info)
    cmd = "ntpdate clock.redhat.com; hwclock -w"
    return execute(cmd, session)


@error_context.context_aware
def update_clksrc(vm, clksrc=None):
    """
    Update linux guest's clocksource and re-boot guest

    :params vm: Virtual machine for vm
    :params clksrc: Expected clocksource, 'kvm-clock' by default
    """
    params = vm.get_params()
    if 'fedora' in params["os_variant"] and clksrc and clksrc != 'kvm-clock':
        cpu_model_flags = params.get["cpu_model_flags"]
        params["cpu_model_flags"] = cpu_model_flags + ",-kvmclock"

    error_context.context("Update guest kernel cli to '%s'" % (clksrc or
                          "kvm-clock"), logging.info)
    utils_test.update_boot_option(vm, args_removed="clocksource=*")
    if clksrc and clksrc != 'kvm-clock':
        boot_option_added = "clocksource=%s" % clksrc
        utils_test.update_boot_option(vm, args_added=boot_option_added)
