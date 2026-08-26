"""Client factory - real API clients or offline simulation, same interface.

pipeline.py receives (github, devin) objects and never knows which mode it
is in. This is also the packaging seam: deploying against GitHub Enterprise
or a Devin enterprise org means swapping config here, not touching pipeline
logic.
"""

import os

from . import config, devin_client, github_client
from .sim import SimDevin, SimGithub


class RealDevin:
    healthcheck = staticmethod(devin_client.healthcheck)
    create_session = staticmethod(devin_client.create_session)
    get_session = staticmethod(devin_client.get_session)
    send_message = staticmethod(devin_client.send_message)


class RealGithub:
    list_open_labeled_issues = staticmethod(github_client.list_open_labeled_issues)
    comment_on_issue = staticmethod(github_client.comment_on_issue)
    get_pull_request = staticmethod(github_client.get_pull_request)
    get_pull_request_files = staticmethod(github_client.get_pull_request_files)
    merge_pull_request = staticmethod(github_client.merge_pull_request)


def is_simulation():
    return os.environ.get("SIMULATE", "") == "1"


def make_clients():
    if is_simulation():
        return SimGithub(), SimDevin()
    config.require("GITHUB_TOKEN", "DEVIN_API_KEY")
    return RealGithub(), RealDevin()
