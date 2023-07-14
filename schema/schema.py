"""Handles JSON schema generation logic"""


import importlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

import click

from samcli.cli.command import _SAM_CLI_COMMAND_PACKAGES
from samcli.lib.config.samconfig import SamConfig


class SchemaKeys(Enum):
    SCHEMA_FILE_NAME = "schema/samcli.json"
    SCHEMA_DRAFT = "http://json-schema.org/draft-04/schema"
    TITLE = "AWS SAM CLI samconfig schema"
    ENVIRONMENT_REGEX = "^.+$"


@dataclass()
class SamCliParameterSchema:
    """Representation of a parameter in the SAM CLI.

    It includes relevant information for the JSON schema, such as name, data type,
    and description, among others.
    """

    name: str
    type: str
    description: str = ""
    default: Optional[Any] = None
    items: Optional[str] = None
    choices: Optional[Any] = None

    def to_schema(self) -> Dict[str, Any]:
        """Return the JSON schema representation of the SAM CLI parameter."""
        param: Dict[str, Any] = {}
        param.update({"title": self.name, "type": self.type, "description": self.description})
        if self.default:
            param.update({"default": self.default})
        if self.items:
            param.update({"items": {"type": self.items}})
        if self.choices:
            param.update({"enum": self.choices})
        return param


@dataclass()
class SamCliCommandSchema:
    """Representation of a command in the SAM CLI.

    It includes relevant information for the JSON schema, such as name, a description of the
    command, and a list of all available parameters.
    """

    name: str  # Full command name, with underscores (i.e. remote_invoke, local_start_lambda)
    description: str
    parameters: List[SamCliParameterSchema]

    def to_schema(self) -> dict:
        """Return the JSON schema representation of the SAM CLI command."""
        COMMANDS_TO_EXCLUDE = [  # TEMPORARY: for use only while generating piece-by-piece
            "deploy",
            "build",
            "local",
            "validate",
            "package",
            "init",
            "delete",
            "bootstrap",
            "list",
            "traces",
            "sync",
            "publish",
            "pipeline",
            "logs",
            "remote",
        ]
        split_cmd_name = self.name.split("_")
        formatted_cmd_name = " ".join(split_cmd_name)
        exclude_params = split_cmd_name[0] in COMMANDS_TO_EXCLUDE
        formatted_params_list = (
            "* " + "\n* ".join([f"{param.name}:\n{param.description}" for param in self.parameters])
            if not exclude_params
            else ""
        )
        params_description = f"Available parameters for the {formatted_cmd_name} command:\n{formatted_params_list}"

        return {
            self.name: {
                "title": f"{formatted_cmd_name.title()} command",
                "description": self.description or "",
                "properties": {
                    "parameters": {
                        "title": f"Parameters for the {formatted_cmd_name} command",
                        "description": params_description,
                        "type": "object",
                        "properties": {param.name: param.to_schema() for param in self.parameters}
                        if not exclude_params
                        else {},
                    },
                },
                "required": ["parameters"],
            }
        }


def clean_text(text: str) -> str:
    """Clean up a string of text to be formatted for the JSON schema."""
    if not text:
        return ""
    return text.replace("\b", "").strip("\n").strip()


def format_param(param: click.core.Option) -> SamCliParameterSchema:
    """Format a click Option parameter to a SamCliParameter object.

    A parameter object should contain the following information that will be
    necessary for including in the JSON schema:
    * name - The name of the parameter
    * help - The parameter's description (may vary between commands)
    * type - The data type accepted by the parameter
      * type.choices - If there are only a certain number of options allowed,
                       a list of those allowed options
    * default - The default option for that parameter
    """
    param_type = param.type.name.lower()
    formatted_param_type = ""
    # NOTE: Params do not have explicit "string" type; either "text" or "path".
    #       All choice options are from a set of strings.
    if param_type in ["text", "path", "choice", "filename", "directory"]:
        formatted_param_type = "string"
    elif param_type == "list":
        formatted_param_type = "array"
    else:
        formatted_param_type = param_type or "string"

    formatted_param: SamCliParameterSchema = SamCliParameterSchema(
        param.name or "",
        formatted_param_type,
        clean_text(param.help or ""),
        items="string" if formatted_param_type == "array" else None,
    )

    if param.default:
        formatted_param.default = list(param.default) if isinstance(param.default, tuple) else param.default

    if param.type.name == "choice" and isinstance(param.type, click.Choice):
        formatted_param.choices = list(param.type.choices)

    return formatted_param


def get_params_from_command(cli) -> List[SamCliParameterSchema]:
    """Given a CLI object, return a list of all parameters in that CLI, formatted as SamCliParameterSchema objects."""
    params_to_exclude = [
        "config_env",  # shouldn't allow different environment from where the config is being read from
        "config_file",  # shouldn't allow reading another file within current file
    ]
    return [
        format_param(param)
        for param in cli.params
        if param.name and isinstance(param, click.core.Option) and param.name not in params_to_exclude
    ]


def retrieve_command_structure(package_name: str) -> List[SamCliCommandSchema]:
    """Given a SAM CLI package name, retrieve its structure.

    Such a structure is the list of all subcommands as `SamCliCommandSchema`, which includes
    the command's name, description, and its parameters.

    Parameters
    ----------
    package_name: str
        The name of the command package to retrieve.

    Returns
    -------
    List[SamCliCommandSchema]
        A list of SamCliCommandSchema objects which represent either a command or a list of
        subcommands within the package.
    """
    module = importlib.import_module(package_name)
    command = []

    if isinstance(module.cli, click.core.Group):  # command has subcommands (e.g. local invoke)
        for subcommand in module.cli.commands.values():
            cmd_name = SamConfig.to_key([module.__name__.split(".")[-1], str(subcommand.name)])
            command.append(
                SamCliCommandSchema(
                    cmd_name,
                    clean_text(subcommand.help or subcommand.short_help or ""),
                    get_params_from_command(subcommand),
                )
            )
    else:
        cmd_name = SamConfig.to_key([module.__name__.split(".")[-1]])
        command.append(
            SamCliCommandSchema(
                cmd_name,
                clean_text(module.cli.help or module.cli.short_help or ""),
                get_params_from_command(module.cli),
            )
        )
    return command


def generate_schema() -> dict:
    """Generate a JSON schema for all SAM CLI commands.

    Returns
    -------
    dict
        A dictionary representation of the JSON schema.
    """
    schema: dict = {}
    commands: List[SamCliCommandSchema] = []

    # Populate schema with relevant attributes
    schema["$schema"] = SchemaKeys.SCHEMA_DRAFT.value
    schema["title"] = SchemaKeys.TITLE.value
    schema["type"] = "object"
    schema["properties"] = {
        # Version number required for samconfig files to be valid
        "version": {"title": "Config version", "type": "number", "default": 0.1}
    }
    schema["required"] = ["version"]
    schema["additionalProperties"] = False
    # Iterate through packages for command and parameter information
    for package_name in _SAM_CLI_COMMAND_PACKAGES:
        commands.extend(retrieve_command_structure(package_name))
    # Generate schema for each of the commands
    schema["patternProperties"] = {SchemaKeys.ENVIRONMENT_REGEX.value: {"title": "Environment", "properties": {}}}
    for command in commands:
        schema["patternProperties"][SchemaKeys.ENVIRONMENT_REGEX.value]["properties"].update(command.to_schema())
    return schema


def write_schema():
    """Generate the SAM CLI JSON schema and write it to file."""
    schema = generate_schema()
    with open(SchemaKeys.SCHEMA_FILE_NAME.value, "w+", encoding="utf-8") as outfile:
        json.dump(schema, outfile, indent=2)


if __name__ == "__main__":
    write_schema()
