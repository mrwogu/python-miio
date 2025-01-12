import enum
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import click

from miio.click_common import EnumType, command, format_output
from miio.device import Device, DeviceStatus
from miio.exceptions import DeviceException

_LOGGER = logging.getLogger(__name__)

MODEL_DISWAHSER_M02 = "viomi.dishwasher.m02"

MODELS_SUPPORTED = [MODEL_DISWAHSER_M02]


class MachineStatus(enum.IntEnum):
    Off = 0
    On = 1
    Running = 2
    Paused = 3
    Done = 4
    Scheduled = 5
    AutoDry = 6


class ProgramStatus(enum.IntEnum):
    Standby = 0
    Prewash = 1
    Wash = 2
    Rinse = 3
    Drying = 4
    Unknown = -1


class Program(enum.IntEnum):
    Standard = 0
    Eco = 1
    Quick = 2
    Intensive = 3
    Glassware = 4
    Sterilize = 7
    Unknown = -1

    @property
    def run_time(self):
        return ProgramRunTime[self.value]


ProgramRunTime = {
    Program.Standard: 7102,
    Program.Eco: 7702,
    Program.Quick: 1675,
    Program.Intensive: 7522,
    Program.Glassware: 6930,
    Program.Sterilize: 8295,
    Program.Unknown: -1,
}


class ChildLockStatus(enum.IntEnum):
    Enabled = 1
    Disabled = 0


class DoorStatus(enum.IntEnum):
    Open = 128
    Closed = 0


class SystemStatus(enum.IntEnum):
    WaterLeak = 1
    InsufficientWaterFlow = 4
    InternalConnectionError = 9
    ThermistorError = 32
    InsufficientWaterSoftener = 512
    HeatingElementError = 2048


class ViomiDishwasherStatus(DeviceStatus):
    def __init__(self, data: Dict[str, Any]) -> None:
        """A ViomiDishwasherStatus representing the most important values for the
        device.

        Example:
            {
                "child_lock": 0,
                "program": 2,
                "run_status": 512,
                "wash_status": 0,
                "wash_temp": 86,
                "power": 0,
                "left_time": 0,
                "wash_done_appointment": 0,
                "freshdry_interval": 0,
                "wash_process": 0
            }
        """

        self.data = data

    @property
    def child_lock(self) -> bool:
        """Returns the child lock status of the device."""
        value = self.data["child_lock"]
        if value in [0, 1]:
            return bool(value)

        raise DeviceException(f"{value} is not a valid child lock status.")

    @property
    def program(self) -> Program:
        """Returns the current selected program of the device."""
        program = self.data["program"]
        try:
            return Program(program)
        except ValueError:
            _LOGGER.warning("Program %r is Unknown.", program)
            return Program.Unknown

    @property
    def door_open(self) -> bool:
        """Returns True if the door is open."""

        return bool(self.data["run_status"] & (1 << 7))

    @property
    def system_status_raw(self) -> int:
        """Returns the raw status number of the device.

        This is in general used to detected:
            - Errors in the system.
            - If the door is open or not.
        """

        return self.data["run_status"]

    @property
    def status(self) -> MachineStatus:
        """Returns the machine status of the device."""

        return MachineStatus(self.data["wash_status"])

    @property
    def temperature(self) -> int:
        """Returns the temperature in degree Celsius as determined by the NTC
        thermistor."""

        return self.data["wash_temp"]

    @property
    def power(self) -> bool:
        """Returns the power status of the device."""

        value = self.data["power"]
        if value in [0, 1]:
            return bool(value)

        raise DeviceException(f"{value} is not a valid power status.")

    @property
    def time_left(self) -> timedelta:
        """Returns the timedelta in seconds of time left of the current program.

        Will always be 0 if no program is running.
        """
        value = self.data["left_time"]
        if isinstance(value, int):
            return timedelta(seconds=value)

        raise DeviceException(f"{value} is not a valid integer for time_left.")

    @property
    def schedule(self) -> Optional[datetime]:
        """Returns a datetime when the scheduled program should be finished.

        Will always be 0 if nothing is scheduled.
        """

        value = self.data["wash_done_appointment"]
        if isinstance(value, int):
            return datetime.fromtimestamp(value) if value else None

        raise DeviceException(
            f"{value} is not a valid integer for wash_done_appointment."
        )

    @property
    def air_refresh_interval(self) -> int:
        """Returns an integer on how often the air in the device should be refreshed.

        Todo:
            * It's unknown what the value means. It seems not to be minutes. The default set by the Xiaomi Home app is 8.
        """

        value = self.data["freshdry_interval"]
        if isinstance(value, int):
            return value

        raise DeviceException(f"{value} is not a valid integer for freshdry_interval.")

    @property
    def program_progress(self) -> ProgramStatus:
        """Returns the program status of the running program."""
        value = self.data["wash_process"]
        try:
            return ProgramStatus(value)
        except ValueError:
            _LOGGER.warning("ProgramStatus %r is Unknown.", value)
            return ProgramStatus.Unknown

    @property
    def errors(self) -> List[SystemStatus]:
        """Returns list of errors if detected in the system."""

        errors = []
        if self.data["run_status"] & (1 << 0):
            errors.append(SystemStatus.WaterLeak)
        if self.data["run_status"] & (1 << 3):
            errors.append(SystemStatus.InternalConnectionError)
        if self.data["run_status"] & (1 << 2):
            errors.append(SystemStatus.InsufficientWaterFlow)
        if self.data["run_status"] & (1 << 5):
            errors.append(SystemStatus.ThermistorError)
        if self.data["run_status"] & (1 << 9):
            errors.append(SystemStatus.InsufficientWaterSoftener)
        if self.data["run_status"] & (1 << 11):
            errors.append(SystemStatus.HeatingElementError)

        return errors


class ViomiDishwasher(Device):
    """Main class representing the dishwasher."""

    _supported_models = MODELS_SUPPORTED

    @command(
        default_output=format_output(
            "",
            "Program: {result.program.name}\n"
            "Program state: {result.program_progress.name}\n"
            "Program time left: {result.time_left}\n"
            "Dishwasher status: {result.status.name}\n"
            "Power status: {result.power}\n"
            "Door open: {result.door_open}\n"
            "Temperature: {result.temperature}\n"
            "Schedule: {result.schedule}\n"
            "Air refresh interval: {result.air_refresh_interval}\n"
            "Child lock: {result.child_lock}\n"
            "System status (raw): {result.system_status_raw}\n"
            "Errors: {result.errors}",
        )
    )
    def status(self) -> ViomiDishwasherStatus:
        """Retrieve properties."""

        properties = [
            "child_lock",
            "program",
            "run_status",
            "wash_status",
            "wash_temp",
            "power",
            "left_time",
            "wash_done_appointment",
            "freshdry_interval",
            "wash_process",
        ]

        values = self.get_properties(properties, max_properties=1)

        return ViomiDishwasherStatus(defaultdict(lambda: None, zip(properties, values)))

    # FIXME: Change these to use the ViomiDishwasherStatus once we can query multiple properties at once (or cache?).
    def _is_on(self) -> bool:
        return bool(self.get_properties(["power"])[0])

    def _is_running(self) -> bool:
        current_status = ProgramStatus(self.get_properties(["wash_process"])[0])
        return current_status > 0

    def _set_wash_status(self, status: MachineStatus) -> Any:
        return self.send("set_wash_status", [status.value])

    @command(default_output=format_output("Powering on"))
    def on(self):
        """Power on."""
        return self.send("set_power", [1])

    @command(default_output=format_output("Powering off"))
    def off(self):
        """Power off."""
        return self.send("set_power", [0])

    @command(
        click.argument("status", type=EnumType(ChildLockStatus)),
        default_output=format_output("Setting child lock to '{status.name}'"),
    )
    def child_lock(self, status: ChildLockStatus):
        """Set child lock."""

        if not self._is_on():
            self.on()
            output = self.send("set_child_lock", [status.value])
            self.off()
        else:
            output = self.send("set_child_lock", [status.value])

        return output

    @command(
        click.argument("time", type=click.DateTime(formats=["%H:%M"])),
        click.argument("program", type=EnumType(Program)),
    )
    def schedule(self, time: datetime, program: Program) -> str:
        """Schedule a program run.

        *time* defines the time when the program should finish.
        """

        if program == Program.Unknown:
            ValueError(f"Program {program.name} is not valid for this function.")

        scheduled_finish_date = datetime.now().replace(
            hour=time.hour, minute=time.minute, second=0, microsecond=0
        )
        scheduled_start_date = scheduled_finish_date - timedelta(
            seconds=program.run_time
        )
        if scheduled_start_date < datetime.now():
            raise ValueError(
                "Proposed time is in the past (the proposed time is the finishing time, not the start time)."
            )

        if not self._is_on():
            self.on()

        if self._is_running():
            raise DeviceException(
                "A wash program is already running. Wait for current program to finish or stop."
            )

        if self.get_properties(["wash_done_appointment"])[0] > 0:
            self.cancel_schedule(check_if_on=False)

        params = f"{round(scheduled_finish_date.timestamp())},{program.value}"
        value = self.send("set_wash_done_appointment", [params])
        _LOGGER.debug(
            "Program %s will start at %s and finish around %s.",
            program.name,
            scheduled_start_date,
            scheduled_finish_date,
        )
        return value

    @command()
    def cancel_schedule(self, check_if_on=True) -> str:
        """Cancel an existing schedule."""

        if not self._is_on() and check_if_on:
            return "Dishwasher is not turned on. Nothing scheduled."

        value = self.send("set_wash_done_appointment", ["0,0"])
        _LOGGER.debug("Schedule cancelled.")
        return value

    @command(
        click.argument("program", type=EnumType(Program), required=False),
    )
    def start(self, program: [Program, None]) -> str:
        """Start a program (with optional program or current)."""

        if program:
            value = self.send("set_program", [program.value])
            _LOGGER.debug("Started program %s.", program.name)
            return value

        if not self._is_on():
            self.on()

        program = Program(self.get_properties(["program"])[0])
        value = self._set_wash_status(MachineStatus.Running)
        _LOGGER.debug("Started program %s.", program.name)
        return value

    @command()
    def stop(self) -> str:
        """Stop a program."""

        if not self._is_running():
            raise DeviceException("No program running.")

        value = self._set_wash_status(MachineStatus.On)
        _LOGGER.debug("Program stopped.")
        return value

    @command()
    def pause(self) -> str:
        """Pause a program."""

        if not self._is_running():
            raise DeviceException("No program running.")

        value = self._set_wash_status(MachineStatus.Paused)
        _LOGGER.debug("Program paused.")
        return value

    @command(name="continue")
    def continue_program(self) -> str:
        """Continue a program."""

        if not self._is_running():
            raise DeviceException("No program running.")

        value = self._set_wash_status(MachineStatus.Running)
        _LOGGER.debug("Program continued.")
        return value

    @command(
        click.argument("time", type=int),
        default_output=format_output("Setting air refresh to '{time}'"),
    )
    def airrefresh(self, time: int) -> List[str]:
        """Set air refresh interval."""

        return self.send("set_freshdry_interval_t", [time])
