# -*- coding: utf-8 -*-
from brain import recommendation_state as rs
from brain import result_state
from brain.task_session import TaskSessionStore
def run(first, second, **kw):
    st = TaskSessionStore()
    p = st.note_recommendation_turn(first, subject=first)
    st.record_candidates((result_state.Candidate(name="ASUS PG27", url="https://e/1"),
                          result_state.Candidate(name="Alienware AW2524HF", url="https://e/2")),
                         evidence=("monitor evidence",))
    same = rs.about_the_same_thing(p, second, **kw)
    st.note_recommendation_turn(second, subject=kw.get("subject", second),
                                **{k:v for k,v in kw.items() if k!="subject"})
    return same, len(st.results().items)
print("CONTINUE (follow_up=True):")
for a,b in [("find me a few good hotels in Seoul","anything cheaper?"),
            ("recommend me some mechanical keyboards","what about the second one?"),
            ("find me a good gaming monitor","open the ASUS one"),
            ("find me some good restaurants in Gangnam","which one would you pick?")]:
    same,n = run(a,b,follow_up=True,subject="")
    print(f"  {b[:32]!r:36} same={same!s:5} candidates={n} {'ok' if same and n==2 else 'BROKEN'}")
print("SWITCH:")
for a,b in [("find me a good gaming monitor","actually find me restaurants in Gangnam"),
            ("find me a good gaming monitor","find me some good restaurants in Gangnam"),
            ("recommend me some mechanical keyboards","find me a good gaming monitor"),
            ("show me hotels in Seoul","what mechanical keyboard should I buy?"),
            ("find me a restaurant","by the way, what does OLED mean?"),
            ("find me some good restaurants in Gangnam","find me a good gaming monitor")]:
    same,n = run(a,b,subject=b)
    print(f"  {b[:36]!r:40} same={same!s:5} candidates={n} {'LEAK' if n else 'ok'}")
