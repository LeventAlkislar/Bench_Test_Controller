# bench_test/recipe/models.py
from dataclasses import dataclass, field
from typing import List


@dataclass
class StepLoop:
    start_step: int
    end_step:   int
    loop_count: int


@dataclass
class RecipeStep:
    port:             int            # Valve A portu (1-8), 0 = değişiklik yok
    duration_minutes: float
    description:      str  = ""
    valve_b_state:    int  = 0       # 0=yok, 1=Load, 2=Inject
    dropview_action:  str  = "none"  # "none" | "start_dropview" | "start_measure" | "stop_measure" | "exit_dropview"
    dropview_scr:     str  = ""      # .scr dosya yolu (boş = mevcut)


@dataclass
class Recipe:
    name:        str
    steps:       List[RecipeStep]
    loop_count:  int             = 1
    step_loops:  List[StepLoop]  = field(default_factory=list)