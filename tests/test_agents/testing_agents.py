from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.agents.visual_agent    import compute_wcag_contrast, compute_apca_lc, VisualAgent
from backend.agents.auditory_agent  import AuditoryAgent, detect_auto_caption
from backend.agents.cognitive_agent import compute_flesch_kincaid_grade, _count_syllables, CognitiveAgent
from backend.agents.motor_agent     import MotorAgent
from backend.agents.at_parsing_agent import ATPArsingAgent, VALID_ARIA_ROLES
from backend.agents.schemas import DisabilityClass
import asyncio

print('=== TEST 1: WCAG contrast + APCA ===')
assert compute_wcag_contrast('#ffffff','#000000') == 21.0
r = compute_wcag_contrast('#888888','#ffffff')
assert r < 4.5, f'#888 should fail 4.5, got {r}'
apca = compute_apca_lc('#767676','#ffffff')
assert apca is not None and isinstance(apca, float)
assert compute_wcag_contrast('rgb(255,255,255)','rgb(0,0,0)') == 21.0
assert compute_wcag_contrast('var(--color)','inherit') is None
print(f'  contrast(white/black)=21.0, #888/white={r} fails 4.5, APCA={apca}, rgb() works, unparseable=None ✅')

print()
print('=== TEST 3: Auto caption detection ===')
assert detect_auto_caption('auto_generated_en.vtt') == True
assert detect_auto_caption('professional_captions.vtt') == False
print('  Caption heuristic ✅')

print()
print('=== TEST 4: Flesch-Kincaid ===')
simple = 'The cat sat on the mat. The dog ran fast. A bird flew high. Kids play games each day. The sun shines bright today.'
fk_s = compute_flesch_kincaid_grade(simple)
complex_t = ('The implementation of asymmetric cryptographic methodologies necessitates consideration of computational complexity boundaries. '
             'Probabilistic polynomial-time algorithms demonstrate fundamental limitations in factorising large semiprime integers. '
             'Contemporary authentication infrastructure utilises hierarchical certificate authority hierarchies for distributed verification processes.')
fk_c = compute_flesch_kincaid_grade(complex_t)
assert fk_s is not None and fk_s < 7.0,  f'simple FK={fk_s}'
assert fk_c is not None and fk_c > 10.0, f'complex FK={fk_c}'
assert compute_flesch_kincaid_grade('Hello.') is None
assert _count_syllables('cat') == 1
assert _count_syllables('implementation') >= 4
print(f'  FK simple={fk_s} (<7), complex={fk_c} (>10), short→None, syllables ✅')

print()
print('=== TEST 5: ARIA roles ===')
for v in ['button','dialog','tab','region','checkbox']:
    assert v in VALID_ARIA_ROLES
for inv in ['togglebutton','popup','accordion']:
    assert inv not in VALID_ARIA_ROLES
print('  ARIA role sets ✅')

print()
print('=== TEST 6: All 5 agents ===')
for AgentCls, expected_dc in [
    (VisualAgent, DisabilityClass.VISUAL),
    (AuditoryAgent, DisabilityClass.AUDITORY),
    (MotorAgent, DisabilityClass.MOTOR),
    (CognitiveAgent, DisabilityClass.COGNITIVE),
    (ATPArsingAgent, DisabilityClass.AT_PARSING),
]:
    a = AgentCls(vllm_endpoint='http://localhost:8000')
    assert a.disability_class == expected_dc
    prompt = a._build_system_prompt([])
    assert len(prompt) > 200
    assert 'criterion_number' in prompt
    asyncio.run(a.close())
    print(f'  {AgentCls.__name__}: dc={a.disability_class.value}, prompt={len(prompt)}c ✅')

print()
print('ALL TESTS PASSED ✅')