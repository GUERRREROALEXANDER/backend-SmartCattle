"""Checks for the list/array example.

The data_structures package is academic and not used by the API, which is
exactly why it had no tests: nothing breaks when it breaks. It is also a
graded deliverable, so a method that quietly does the wrong thing costs marks
rather than uptime.
"""

import pytest

from data_structures.array_smartcattle import DetectedAnimal, DetectedAnimalList


def animal(animal_id: str, zone: str = "pasture-north", status: str = "inside_safe_zone"):
    return DetectedAnimal(animal_id, zone, status)


def test_an_empty_list_has_no_length():
    assert len(DetectedAnimalList()) == 0


def test_added_animals_are_counted_and_found():
    animals = DetectedAnimalList()
    animals.add(animal("Cow-001"))
    animals.add(animal("Cow-002"))
    assert len(animals) == 2
    assert animals.find("Cow-002").animal_id == "Cow-002"


def test_a_duplicate_id_is_refused():
    # The identity of an animal has to be unique, or two detections of
    # different animals merge into one record.
    animals = DetectedAnimalList()
    animals.add(animal("Cow-001"))
    with pytest.raises(ValueError, match="Cow-001"):
        animals.add(animal("Cow-001", zone="water-trough"))
    assert len(animals) == 1


def test_an_absent_id_is_not_found():
    assert DetectedAnimalList().find("Cow-404") is None


def test_update_changes_only_what_it_is_given():
    animals = DetectedAnimalList()
    animals.add(animal("Cow-001", zone="pasture-north", status="inside_safe_zone"))

    assert animals.update("Cow-001", status="out_of_zone") is True
    found = animals.find("Cow-001")
    assert found.status == "out_of_zone"
    # The zone was not passed, so it must be untouched.
    assert found.zone == "pasture-north"

    assert animals.update("Cow-001", zone="water-trough") is True
    assert animals.find("Cow-001").zone == "water-trough"
    assert animals.find("Cow-001").status == "out_of_zone"


def test_updating_an_absent_animal_reports_failure():
    # Returns False rather than raising, so a caller can tell "not there" from
    # "changed nothing".
    assert DetectedAnimalList().update("Cow-404", status="out_of_zone") is False


def test_remove_reports_whether_it_removed():
    animals = DetectedAnimalList()
    animals.add(animal("Cow-001"))
    assert animals.remove("Cow-001") is True
    assert animals.remove("Cow-001") is False
    assert len(animals) == 0


def test_removing_the_middle_keeps_the_rest_in_order():
    # The index-based delete is the one that can corrupt the sequence.
    animals = DetectedAnimalList()
    for index in range(1, 4):
        animals.add(animal(f"Cow-00{index}"))
    assert animals.remove("Cow-002") is True
    assert [found.animal_id for found in (animals.find("Cow-001"), animals.find("Cow-003"))] == [
        "Cow-001", "Cow-003",
    ]
    assert animals.find("Cow-002") is None
    assert len(animals) == 2


def test_count_by_status_counts_only_that_status():
    animals = DetectedAnimalList()
    animals.add(animal("Cow-001", status="inside_safe_zone"))
    animals.add(animal("Cow-002", status="out_of_zone"))
    animals.add(animal("Cow-003", status="out_of_zone"))
    assert animals.count_by_status("out_of_zone") == 2
    assert animals.count_by_status("inside_safe_zone") == 1
    assert animals.count_by_status("lost") == 0


def test_show_prints_every_animal_with_its_index(capsys):
    animals = DetectedAnimalList()
    animals.add(animal("Cow-001"))
    animals.add(animal("Cow-002"))
    animals.show()
    printed = capsys.readouterr().out
    assert "[0]" in printed and "Cow-001" in printed
    assert "[1]" in printed and "Cow-002" in printed
