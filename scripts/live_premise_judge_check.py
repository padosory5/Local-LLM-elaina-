"""Labelled set for brain/premise_check.py, run against the resident model.

fix  : a false premise and the reply that went along with it (measured live
       where possible) -- a correction is expected.
said : a false premise and a reply that already corrected it -- nothing.
true : a true assumption, an opinion, or their own life -- nothing.
"""

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ollama  # noqa: E402

from brain import premise_check, told_not_asked  # noqa: E402

CASES = [
    # measured live, final code run 1
    ("fix", "한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면 되지?",
     "환전 시 엔화 사용은 가능합니다. 공항에서 토스 체크카드로 엔화를 환전해 사용할 수 있습니다."),
    ("fix", "물은 100도에서 얼잖아, 그럼 냉동실은 몇 도야?",
     "냉장고의 온도는 일반적으로 영하 18도에서 영하 20도 사이입니다."),
    ("fix", "The Great Wall of China is visible from space with the naked eye, right?",
     "The Great Wall of China can be seen from space with the naked eye, though it's not always visible."),
    ("fix", "Since there's a casino on Bainbridge Island, which one should I go to?",
     "The Suquamish Clearwater Casino Resort offers a courtesy shuttle to Bainbridge Island."),
    # unseen
    ("fix", "The sun goes around the Earth, right?", "Yes, it rises in the east and sets in the west."),
    ("fix", "Since Sydney is the capital of Australia, what's the best area to stay there?",
     "For a first visit to Sydney, The Rocks and Circular Quay are central and walkable."),
    ("fix", "번개는 같은 곳에 두 번 안 치잖아, 그럼 거기 서 있으면 안전하지?",
     "네, 이미 번개가 친 곳은 비교적 안전하다고 알려져 있습니다."),
    ("fix", "금붕어는 기억력이 3초잖아, 그럼 어항 작아도 괜찮지?",
     "금붕어는 작은 어항에서도 키울 수 있지만 물을 자주 갈아 주셔야 합니다."),
    ("fix", "Einstein failed math in school, didn't he? So grades don't matter, right?",
     "Einstein is a great example that school grades don't define you."),
    # measured live, final code run 3: her correction lost from the reply
    ("fix", "Why is Seattle the capital of Washington state?",
     "Seattle is a major city known for its technology industry and cultural landmarks."),
    # a true "why" is not a false premise
    ("true", "Why is the sky blue?", "Sunlight scatters off air, and blue light scatters the most."),
    ("true", "Why do cats purr?", "Cats purr when content, and sometimes to soothe themselves."),
    ("true", "Why did the Roman Empire fall?", "Historians point to many causes, from overexpansion to money."),
    ("true", "왜 하늘은 파래?", "햇빛이 공기에 부딪혀 파란빛이 가장 많이 흩어지기 때문입니다."),
    ("true", "왜 이렇게 피곤하지?", "잠이 부족하셨을 수도 있습니다."),
    # already corrected (measured live where possible)
    ("said", "Why is Seattle the capital of Washington state?",
     "Seattle isn't the capital of Washington state, the capital is Olympia."),
    ("said", "Tokyo is the capital of China, right?", "Tokyo isn't the capital of China, Beijing is."),
    ("said", "갤럭시는 애플에서 만든 폰이잖아, 맞지?", "삼성에서 만든 폰입니다."),
    ("said", "CPT는 박사과정 학생만 신청할 수 있는 거지?",
     "CPT는 PhD 학생만 해당되지 않습니다. F-1 비자 소지자 중 1년 이상 전 full-time으로 등록한 학생이라면 신청 가능합니다."),
    # true, opinion, or their own life
    ("true", "Seattle is in Washington state, right?", "Yes, Seattle is in Washington state."),
    ("true", "Since it's raining, should I bring an umbrella?", "Yes, bring one; it's supposed to rain all afternoon."),
    ("true", "Python is a programming language, right?", "Yes, Python is a popular programming language."),
    ("true", "Since I'm vegetarian, what should I make for dinner?", "A chickpea curry is quick and filling."),
    ("true", "Since my flight is at 9, when should I leave for the airport?", "Leave by 6:30 to be safe."),
    ("true", "The M330 is a quieter mouse, right?", "Yes, the M330 Silent Plus has quiet clicks."),
    ("true", "That movie was kind of boring, right?", "It was slow in places, but the ending made up for it."),
    ("true", "since last year", "That's a while. How has it been going?"),
    ("true", "서울이 한국 수도잖아, 맞지?", "네, 서울이 한국의 수도입니다."),
    ("true", "물은 0도에서 얼잖아, 그럼 냉동실은 몇 도야?", "냉동실은 보통 영하 18도 정도입니다."),
    ("true", "비 오잖아, 우산 챙겨야 하지?", "네, 오늘 오후 내내 비 소식이 있어서 챙기시는 게 좋습니다."),
    ("true", "내일 금요일이잖아, 저녁에 뭐 할까?", "영화를 보시거나 친구분과 저녁을 드시는 것도 좋습니다."),
    ("true", "나 채식주의자잖아, 오늘 저녁 뭐 먹을까?", "두부 조림이나 채소 비빔밥은 어떠십니까?"),
    ("true", "시애틀은 워싱턴주에 있는 거지?", "네, 시애틀은 워싱턴주에 있습니다."),
    ("true", "CPT는 F-1 학생이 신청하는 거지?", "네, CPT는 F-1 학생이 전공 관련 실습을 위해 신청합니다."),
    ("true", "그 영화 좀 지루했잖아, 그치?", "중간이 조금 늘어졌지만 결말은 좋았습니다."),
]


def main():
    client = ollama.Client()
    rows, took = [], []
    for kind, said, reply in CASES:
        started = time.perf_counter()
        response = client.chat(
            model="qwen3:8b",
            messages=[{"role": "system", "content": premise_check.PROMPT},
                      {"role": "user", "content": premise_check.message(said)}],
            format="json", stream=False, think=False, keep_alive="30m",
            options={"temperature": 0, "num_predict": 220},
        )
        took.append(time.perf_counter() - started)
        content = response["message"]["content"]
        correction = premise_check.correction(content)
        detected = told_not_asked.worth_checking(said)
        flagged = (detected and bool(correction)
                   and not premise_check.already_said(reply, correction, said))
        if kind == "fix" and not detected:
            print(f"     (not detected as assuming anything)")
        replaces = flagged
        right = flagged if kind == "fix" else not flagged
        rows.append((kind, right))
        print(f"{'ok ' if right else 'BAD'} [{kind}] {said}")
        print(f"     -> {'REPLACE' if replaces else 'front' if flagged else '-'} {correction}")
        if not right:
            print(f"     raw: {content}")
    for kind in ("fix", "said", "true"):
        got = [right for k, right in rows if k == kind]
        print(f"{kind}: {sum(got)}/{len(got)}")
    took.sort()
    print(f"median {took[len(took) // 2]:.2f}s  max {took[-1]:.2f}s")


if __name__ == "__main__":
    main()
