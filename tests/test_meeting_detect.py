"""v0.6 D: which apps count as a meeting, and one prompt per call."""

import pytest

from hemsa import meeting_detect as md


@pytest.mark.parametrize("key, name", [
    ("C:#Users#x#AppData#Roaming#Zoom#bin#Zoom.exe", "Zoom"),
    ("MSTeams_8wekyb3d8bbwe", "Teams"),
    ("5319275A.WhatsAppDesktop_cv1g1gvanyjgm", "WhatsApp"),
    ("C:#Program Files#Google#Chrome#Application#chrome.exe", None),   # browsers: never
    ("C:#Users#x#AppData#Local#Programs#Hemsa#Hemsa.exe", None),       # ourselves: never
    ("Microsoft.WindowsSoundRecorder_8wekyb3d8bbwe", None),
])
def test_known_meeting_apps_only(key, name):
    assert md.name_for(key) == name


def test_one_report_per_call():
    d = md.Detector()
    assert d.poll({"Teams"}) == ["Teams"]
    assert d.poll({"Teams"}) == []                 # still on the call
    assert d.poll({"Teams", "Zoom"}) == ["Zoom"]
    assert d.poll(set()) == []
    assert d.poll({"Teams"}) == ["Teams"]          # a NEW call after letting go


def test_scan_never_raises_on_this_machine():
    assert isinstance(md.meeting_apps_on_mic(), set)
