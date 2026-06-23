"""Tests for processing_status field in BaseUKF."""

import pytest

from ahvn.ukf.base import BaseUKF


def test_processing_status_when_init():
    ukf = BaseUKF(name="ukf1")
    assert ukf.is_processing is False


def test_set_processing_status_invalid():
    ukf = BaseUKF(name="ukf1")
    with pytest.raises(ValueError):
        ukf.set_processing_status("invalid")


def test_set_processing_status_succeed():
    ukf = BaseUKF(name="ukf1")
    ukf.set_processing_status("upserting")
    assert ukf.processing_status == "upserting"
    assert ukf.is_processing is True


def test_unset_processing_status():
    ukf = BaseUKF(name="ukf1", processing_status="upserting")
    assert ukf.is_processing is True
    ukf.unset_processing_status()
    assert ukf.is_processing is False
