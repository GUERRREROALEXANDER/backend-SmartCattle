"""Academic example of how a list could track current SmartCattle detections.

YOLO only detects the class "cow". IDs such as "Cow-001" assume future
identification (for example, an ear tag), which is not implemented.
"""

from dataclasses import dataclass


@dataclass
class DetectedAnimal:
    animal_id: str
    zone: str
    status: str


class DetectedAnimalList:
    def __init__(self) -> None:
        self._animals: list[DetectedAnimal] = []

    def add(self, animal: DetectedAnimal) -> None:
        if self.find(animal.animal_id) is not None:
            raise ValueError(f"Duplicate animal_id: {animal.animal_id}")
        self._animals.append(animal)

    def find(self, animal_id: str) -> DetectedAnimal | None:
        # Linear search is O(n): the requested ID may be at the end.
        for animal in self._animals:
            if animal.animal_id == animal_id:
                return animal
        return None

    def update(
        self, animal_id: str, zone: str | None = None, status: str | None = None
    ) -> bool:
        animal = self.find(animal_id)
        if animal is None:
            return False
        if zone is not None:
            animal.zone = zone
        if status is not None:
            animal.status = status
        return True

    def remove(self, animal_id: str) -> bool:
        for index, animal in enumerate(self._animals):
            if animal.animal_id == animal_id:
                del self._animals[index]
                return True
        return False

    def show(self) -> None:
        for index in range(len(self._animals)):
            print(f"[{index}] {self._animals[index]}")

    def count_by_status(self, status: str) -> int:
        count = 0
        for animal in self._animals:
            if animal.status == status:
                count += 1
        return count

    def __len__(self) -> int:
        return len(self._animals)


if __name__ == "__main__":
    animals = DetectedAnimalList()
    animals.add(DetectedAnimal("Cow-001", "pasture-north", "inside_safe_zone"))
    animals.add(DetectedAnimal("Cow-002", "water-trough", "inside_safe_zone"))
    animals.add(DetectedAnimal("Cow-003", "pasture-north", "inside_safe_zone"))
    print(f"Currently detected animals: {len(animals)}")
    animals.show()
    print(f"Find Cow-002: {animals.find('Cow-002')}")
    print(f"Update Cow-003: {animals.update('Cow-003', status='out_of_zone')}")
    print(f"Animals out of zone: {animals.count_by_status('out_of_zone')}")
    print(f"Remove Cow-001: {animals.remove('Cow-001')}")
    animals.show()
    print(f"Find Cow-099: {animals.find('Cow-099')}")
