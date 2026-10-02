"""Personalized bedtime storyteller with classification, review, and history."""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from database import (
    get_child_profile,
    get_latest_initial_request,
    get_or_create_user,
    get_recent_stories,
    initialize_database,
    new_story_id,
    save_child_profile,
    save_message,
)


"""
Before submitting the assignment, describe here in a few sentences what you
would have built next if you spent 2 more hours on this project:

I would build a small evaluation set covering different ages, story categories,
unsafe requests, and user revisions. I would compare first drafts with judged
revisions, tune the scoring threshold using human review, and add a compact UI
that makes the story and its revision history easy to inspect.
"""


MODEL = "gpt-3.5-turbo"  # Required by the assignment. Do not change.
MAX_JUDGE_REVISIONS = 2
MAX_USER_CHANGES = 5
MIN_OVERALL_SCORE = 8.0
READ_ALOUD_WORDS_PER_MINUTE = 130
SURPRISE_STORY_REQUEST = """
Create an original surprise bedtime story for ages 5 to 10. Choose the
protagonist, story world, emotional theme, gentle conflict, and useful everyday
action creatively. The protagonist may be a child, animal, magical creature, or
an unexpected combination. Make the idea feel intentional rather than like a
collection of random objects. Avoid repeating the plot, special object, helper,
resolution, or recurring phrase from the returning user's recent stories.
""".strip()
SCORE_WEIGHTS = {
    "age_fit": 0.20,
    "personalization": 0.15,
    "learning_through_action": 0.15,
    "story_arc": 0.20,
    "bedtime_pacing": 0.20,
    "originality": 0.10,
}
CRITICAL_SCORES = {"age_fit", "story_arc", "bedtime_pacing"}

BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"
load_dotenv(BASE_DIR / ".env")


def load_prompt(file_name: str) -> str:
    return (PROMPT_DIR / file_name).read_text(encoding="utf-8").strip()


def show_wait_message(message: str) -> None:
    """Display progress before a blocking model call."""
    print(f"\n{message}", flush=True)


CATEGORIES = json.loads(load_prompt("arcs.json"))
CLASSIFIER_PROMPT_TEMPLATE = load_prompt("classifier.txt")
CONVERSATION_SYSTEM_PROMPT = load_prompt("conversation.txt")
STORYTELLER_SYSTEM_PROMPT = load_prompt("storyteller.txt")
JUDGE_SYSTEM_PROMPT = load_prompt("judge.txt")


def call_model(
    prompt: str,
    max_tokens: int = 3000,
    temperature: float = 0.1,
    system_prompt: Optional[str] = None,
) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        stream=False,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("The model returned an empty response.")
    return content.strip()


def parse_json_object(raw_text: str) -> Dict:
    """Parse JSON even if the model accidentally adds a Markdown code fence."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise TypeError("Expected a JSON object.")
    return result


def categorize_request(request: str) -> Dict:
    prompt = CLASSIFIER_PROMPT_TEMPLATE.replace(
        "__CATEGORIES__", ", ".join(CATEGORIES)
    ).replace("__REQUEST__", request)

    try:
        result = parse_json_object(call_model(prompt, max_tokens=750, temperature=0.0))
    except (json.JSONDecodeError, KeyError, TypeError):
        result = {}

    category = result.get("category", "imaginative_play")
    if category not in CATEGORIES:
        category = "imaginative_play"

    adaptation = result.get("adaptation")
    try:
        validate_adaptation_brief(adaptation)
    except (KeyError, TypeError, ValueError):
        adaptation = None

    acknowledgement = result.get("acknowledgement")
    if not isinstance(acknowledgement, str) or not acknowledgement.strip():
        acknowledgement = (
            "I’ve got it. I’ll keep the details you shared at the heart of "
            "the story and let it settle into a gentle ending."
        )

    child_profile = normalize_child_profile(result.get("child_profile"))
    visual_motif = normalize_visual_motif(result.get("visual_motif"))

    return {
        "category": category,
        "world": result.get("world", "mixed"),
        "age_band": result.get("age_band", "unspecified"),
        "energy": result.get("energy", "very_calm"),
        "learning_theme": result.get("learning_theme", "none"),
        "acknowledgement": acknowledgement.strip(),
        "child_profile": child_profile,
        "visual_motif": visual_motif,
        "adaptation": adaptation,
    }


def normalize_child_profile(value: object) -> Dict:
    """Return a bounded child profile for UI personalization."""
    if not isinstance(value, dict):
        value = {}
    name = value.get("name")
    age = value.get("age")
    interests = value.get("interests")
    emotional_context = value.get("emotional_context")
    return {
        "name": name.strip()[:80] if isinstance(name, str) and name.strip() else None,
        "age": age if type(age) is int and 5 <= age <= 10 else None,
        "interests": [
            item.strip()[:80]
            for item in interests[:6]
            if isinstance(item, str) and item.strip()
        ] if isinstance(interests, list) else [],
        "emotional_context": (
            emotional_context.strip()[:160]
            if isinstance(emotional_context, str) and emotional_context.strip()
            else None
        ),
    }


def merge_child_profiles(remembered: object, current: object) -> Dict:
    """Merge newly supplied facts over a returning user's stored profile."""
    old = normalize_child_profile(remembered)
    new = normalize_child_profile(current)
    interests = []
    for interest in [*new["interests"], *old["interests"]]:
        if interest.casefold() not in {item.casefold() for item in interests}:
            interests.append(interest)
    return {
        "name": new["name"] or old["name"],
        "age": new["age"] or old["age"],
        "interests": interests[:6],
        "emotional_context": new["emotional_context"] or old["emotional_context"],
    }


def child_profile_has_memory(profile: Dict) -> bool:
    return bool(
        profile.get("name")
        or profile.get("age")
        or profile.get("interests")
        or profile.get("emotional_context")
    )


def normalize_visual_motif(value: object) -> Dict:
    """Constrain model-selected visuals to animation primitives the UI supports."""
    allowed_motion = {"fly", "float", "bob", "walk", "swim", "twinkle", "sway", "settle"}
    allowed_mood = {"gentle", "playful", "dreamy", "cozy"}
    if not isinstance(value, dict):
        value = {}
    glyphs = value.get("glyphs")
    clean_glyphs = [
        glyph.strip()[:16]
        for glyph in glyphs[:4]
        if isinstance(glyph, str) and glyph.strip()
    ] if isinstance(glyphs, list) else []
    if len(clean_glyphs) == 1:
        clean_glyphs = clean_glyphs * 3
    elif len(clean_glyphs) == 2:
        clean_glyphs.append(clean_glyphs[0])
    motion = value.get("motion")
    mood = value.get("mood")
    label = value.get("accessible_label")
    return {
        "glyphs": clean_glyphs or ["✨", "🌙", "✨"],
        "motion": motion if motion in allowed_motion else "float",
        "mood": mood if mood in allowed_mood else "dreamy",
        "accessible_label": (
            label.strip()[:160]
            if isinstance(label, str) and label.strip()
            else "Gentle storybook shapes drifting beneath the moon"
        ),
    }


def history_excerpt(recent_stories: List[Dict]) -> str:
    if not recent_stories:
        return "No previous stories are available."

    excerpts = []
    for number, story in enumerate(recent_stories, start=1):
        compact = " ".join(story["content"].split())[:700]
        excerpts.append(
            f"Previous story {number} ({story.get('category') or 'uncategorized'}): "
            f"{compact}"
        )
    return "\n".join(excerpts)


def generate_story(request: str, classification: Dict, recent_stories: List[Dict]) -> str:
    category = classification["category"]
    prompt = f"""
Parent request:
{request}

Story classification:
{json.dumps(classification, indent=2)}

Tailored arc for {category}:
{CATEGORIES[category]}

Recent stories for this returning user:
{history_excerpt(recent_stories)}

Honor explicit preferences, but create a different combination of problem,
events, special object, resolution, and repeated phrase from recent stories.
Follow the age-specific vocabulary and dialogue guidance in your storytelling
instructions. Let the complete story arc determine when the story ends.
""".strip()
    return call_model(
        prompt,
        max_tokens=1800,
        temperature=0.8,
        system_prompt=STORYTELLER_SYSTEM_PROMPT,
    )


def judge_story(
    request: str,
    story: str,
    classification: Dict,
    recent_stories: List[Dict],
) -> Dict:
    prompt = f"""
Original parent request and later feedback:
{request}

Classification and intended strategy:
{json.dumps(classification, indent=2)}

Recent-story excerpts for originality comparison:
{history_excerpt(recent_stories)}

Candidate story:
{story}

""".strip()

    raw_review = call_model(
        prompt,
        max_tokens=800,
        temperature=0.0,
        system_prompt=JUDGE_SYSTEM_PROMPT,
    )
    try:
        review = parse_json_object(raw_review)
    except (json.JSONDecodeError, TypeError):
        repair_prompt = (
            "Convert the following review into only the exact JSON schema "
            "specified in your instructions. Do not change its assessment:\n\n"
            + raw_review
        )
        review = parse_json_object(
            call_model(
                repair_prompt,
                max_tokens=800,
                temperature=0.0,
                system_prompt=JUDGE_SYSTEM_PROMPT,
            )
        )
    review["story_visual"] = normalize_visual_motif(
        review.get("story_visual")
    )
    return review


def calculate_overall_score(review: Dict) -> float:
    """Convert weighted 1-5 rubric scores into an explicit score out of 10."""
    scores = review.get("scores", {})
    if not isinstance(scores, dict) or not set(SCORE_WEIGHTS).issubset(scores):
        return 0.0
    if any(type(scores[name]) is not int or not 1 <= scores[name] <= 5 for name in SCORE_WEIGHTS):
        return 0.0

    weighted_average = sum(
        scores[name] * weight for name, weight in SCORE_WEIGHTS.items()
    )
    return round(weighted_average * 2, 1)


def review_passed(review: Dict) -> bool:
    scores = review.get("scores", {})
    overall_score = calculate_overall_score(review)
    return bool(
        review.get("safe") is True
        and isinstance(scores, dict)
        and overall_score >= MIN_OVERALL_SCORE
        and all(scores.get(name, 0) >= 4 for name in CRITICAL_SCORES)
        and all(scores.get(name, 0) >= 3 for name in SCORE_WEIGHTS)
    )


def revise_story(
    request: str,
    current_story: str,
    classification: Dict,
    changes: List[str],
    user_feedback: Optional[str] = None,
) -> str:
    prompt = f"""
Original request and accumulated parent feedback:
{request}

Classification:
{json.dumps(classification, indent=2)}

Current story:
{current_story}

Newest parent-requested change:
{user_feedback or 'No additional parent feedback.'}

Judge's required changes:
{json.dumps(changes, indent=2)}

Revise the story. Preserve its successful details, apply the parent's safe
request, fix every judge issue, and return only the complete title and story.
""".strip()
    return call_model(
        prompt,
        max_tokens=1800,
        temperature=0.5,
        system_prompt=STORYTELLER_SYSTEM_PROMPT,
    )


def improve_until_acceptable(
    user_id: int,
    story_id: str,
    request: str,
    story: str,
    classification: Dict,
    recent_stories: List[Dict],
) -> str:
    category = classification["category"]
    current_story = story

    for attempt in range(MAX_JUDGE_REVISIONS + 1):
        save_message(user_id, story_id, "assistant", current_story, category)
        if attempt == 0:
            show_wait_message(
                "I have a draft. I’m giving it one quiet bedtime check…"
            )
        else:
            show_wait_message(
                "The new draft is ready. I’m checking that it still feels "
                "gentle and complete…"
            )
        review = judge_story(request, current_story, classification, recent_stories)
        word_count = len(current_story.split())
        estimated_minutes = round(
            word_count / READ_ALOUD_WORDS_PER_MINUTE,
            1,
        )
        review["overall_score"] = calculate_overall_score(review)
        review["word_count"] = word_count
        review["estimated_read_aloud_minutes"] = estimated_minutes
        save_message(user_id, story_id, "judge", json.dumps(review), category)
        print(
            f"Bedtime quality score: {review['overall_score']}/10 "
            f"({word_count} words, about {estimated_minutes} minutes aloud)"
        )

        if review_passed(review):
            return current_story

        if attempt == MAX_JUDGE_REVISIONS:
            if review.get("safe") is not True:
                raise RuntimeError("No safe story passed review.")
            return current_story

        print("My story editor found a few things I can make gentler and clearer.")
        print(
            f"I’m revising it now ({attempt + 1}/{MAX_JUDGE_REVISIONS})…",
            flush=True,
        )
        required_changes = list(review.get("changes") or [])
        current_story = revise_story(
            request,
            current_story,
            classification,
            required_changes or ["Improve the low-scoring rubric dimensions."],
        )

    return current_story


def read_story_request() -> str:
    """Collect a request safely when the parent pastes multiple lines."""
    print(
        "\nTell me about the bedtime story you’d like tonight. You can share "
        "your child’s name, age, favorite things, and anything they’re learning "
        "or feeling.\n"
        "Write as much as you like, then press Enter on an empty line. Or press "
        "Enter now and I’ll surprise you:"
    )
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)


def resolve_story_request(request: str) -> tuple:
    """Treat a blank request as an explicit request for a creative surprise."""
    cleaned = request.strip()
    if cleaned:
        return cleaned, False
    return SURPRISE_STORY_REQUEST, True


def understand_conversation_turn(
    original_request: str,
    current_story: str,
    conversation: List[Dict[str, str]],
    latest_message: str,
    child_profile: Optional[Dict] = None,
) -> Dict:
    """Use the model to understand a parent naturally and in story context."""
    context = {
        "original_request": original_request,
        "current_story": current_story,
        "remembered_child_profile": normalize_child_profile(child_profile),
        "recent_conversation": conversation[-6:],
        "latest_parent_message": latest_message,
    }
    raw_response = call_model(
        json.dumps(context, ensure_ascii=False),
        max_tokens=350,
        temperature=0.2,
        system_prompt=CONVERSATION_SYSTEM_PROMPT,
    )

    try:
        turn = parse_json_object(raw_response)
        normalize_conversation_turn(turn)
        validate_conversation_turn(turn)
        return turn
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        repair_prompt = f"""
Convert this response into the exact JSON schema in your system instructions.
Preserve its meaning. Return JSON only.

Response to repair:
{raw_response}
""".strip()
        turn = parse_json_object(
            call_model(
                repair_prompt,
                max_tokens=350,
                temperature=0.0,
                system_prompt=CONVERSATION_SYSTEM_PROMPT,
            )
        )
        normalize_conversation_turn(turn)
        validate_conversation_turn(turn)
        return turn


def normalize_conversation_turn(turn: Dict) -> None:
    """Make routing conservative before it is allowed to trigger generation."""
    if turn.get("intent") == "revise" and str(turn.get("assistant_reply", "")).rstrip().endswith("?"):
        turn["intent"] = "clarify"
        turn["revision_request"] = None
        turn["adaptation"] = None
    update = turn.get("profile_update")
    turn["profile_update"] = normalize_child_profile(update) if isinstance(update, dict) else None


def validate_conversation_turn(turn: Dict) -> None:
    """Reject malformed routing decisions before they control the workflow."""
    expected_keys = {
        "intent",
        "assistant_reply",
        "revision_request",
        "adaptation",
        "profile_update",
    }
    if set(turn) != expected_keys:
        raise ValueError("Conversation response has unexpected fields.")
    if turn["intent"] not in {"revise", "accept", "discuss", "clarify"}:
        raise ValueError("Conversation response has an unknown intent.")
    if not isinstance(turn["assistant_reply"], str) or not turn["assistant_reply"].strip():
        raise ValueError("Conversation response needs an assistant reply.")
    revision = turn["revision_request"]
    if turn["intent"] == "revise":
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("A revision intent needs a revision request.")
        validate_adaptation_brief(turn["adaptation"])
    elif revision is not None:
        raise ValueError("Only a revision intent may include a revision request.")
    elif turn["adaptation"] is not None:
        raise ValueError("Only a revision intent may include an adaptation brief.")
    if turn["profile_update"] is not None and not child_profile_has_memory(turn["profile_update"]):
        turn["profile_update"] = None


def validate_adaptation_brief(brief: Optional[Dict]) -> None:
    """Validate a runtime creative brief without tying it to any named source."""
    if brief is None:
        return
    expected_keys = {
        "source_or_concept",
        "emotional_core",
        "narrative_pattern",
        "elements_to_soften",
        "child_safe_reimagining",
    }
    if not isinstance(brief, dict) or set(brief) != expected_keys:
        raise ValueError("Adaptation brief has unexpected fields.")
    for name in ("source_or_concept", "narrative_pattern", "child_safe_reimagining"):
        if not isinstance(brief[name], str) or not brief[name].strip():
            raise ValueError(f"Adaptation field {name} must contain text.")
    themes = brief["emotional_core"]
    if not isinstance(themes, list) or not 1 <= len(themes) <= 3:
        raise ValueError("Adaptation emotional_core must contain one to three themes.")
    if any(not isinstance(value, str) or not value.strip() for value in themes):
        raise ValueError("Adaptation emotional_core contains invalid text.")

    soften = brief["elements_to_soften"]
    if not isinstance(soften, list) or len(soften) > 5:
        raise ValueError("Adaptation elements_to_soften must be a short list.")
    if any(not isinstance(value, str) or not value.strip() for value in soften):
        raise ValueError("Adaptation elements_to_soften contains invalid text.")


def main() -> None:
    initialize_database()

    print("\nWelcome to the Quiet Storyteller.")
    print("Together, we’ll make a gentle story that settles toward sleep.\n")
    user_name = input("First, what should I call you? ").strip()
    if not user_name:
        print("I’ll need a name or nickname so I can remember your story history.")
        return

    try:
        user_id = get_or_create_user(user_name)
        recent_stories = get_recent_stories(user_id)
        remembered_profile = get_child_profile(user_id)
        if not child_profile_has_memory(remembered_profile):
            previous_request = get_latest_initial_request(user_id)
            if previous_request:
                remembered_profile = categorize_request(previous_request).get(
                    "child_profile",
                    {},
                )
        if recent_stories:
            print(f"\nWelcome back, {user_name}. I remember your recent stories.")
        else:
            print(f"\nIt’s lovely to meet you, {user_name}.")

        request, surprise_requested = resolve_story_request(read_story_request())

        story_id = new_story_id()
        show_wait_message("Hmm… let me think about that for a moment.")
        classification = categorize_request(request)
        classification["child_profile"] = merge_child_profiles(
            remembered_profile,
            classification.get("child_profile"),
        )
        acknowledgement = classification.pop("acknowledgement")
        category = classification["category"]
        save_message(user_id, story_id, "user", request, category)
        if child_profile_has_memory(classification["child_profile"]):
            save_child_profile(
                user_id,
                story_id,
                classification["child_profile"],
            )

        if surprise_requested:
            print(
                "\nNo details tonight? I’ll surprise you with something new, "
                "gentle, and cozy."
            )
        else:
            print(f"\n{acknowledgement}")
        show_wait_message(
            "I’m cooking up the characters, a gentle little problem, and a "
            "cozy ending…"
        )
        first_draft = generate_story(request, classification, recent_stories)
        active_request = request
        current_story = improve_until_acceptable(
            user_id,
            story_id,
            active_request,
            first_draft,
            classification,
            recent_stories,
        )
        print("\nHere’s tonight’s story:\n")
        print(current_story)

        changes_made = 0
        conversation: List[Dict[str, str]] = []
        while changes_made < MAX_USER_CHANGES:
            reply = input(
                "\nHow does that feel? You can ask me about it, request any "
                "change, or tell me when it feels ready: "
            ).strip()
            if not reply:
                break

            save_message(user_id, story_id, "user", reply, category)
            show_wait_message(
                "Let me think about how that should shape the story…"
            )
            turn = understand_conversation_turn(
                request,
                current_story,
                conversation,
                reply,
                classification.get("child_profile"),
            )
            if turn["profile_update"] is not None:
                classification["child_profile"] = merge_child_profiles(
                    classification.get("child_profile"),
                    turn["profile_update"],
                )
                save_child_profile(
                    user_id,
                    story_id,
                    classification["child_profile"],
                )
            assistant_reply = turn["assistant_reply"].strip()
            print(f"\n{assistant_reply}")
            save_message(
                user_id,
                story_id,
                "assistant",
                assistant_reply,
                "conversation",
            )
            conversation.extend(
                [
                    {"role": "parent", "content": reply},
                    {"role": "assistant", "content": assistant_reply},
                ]
            )

            if turn["intent"] == "accept":
                break
            if turn["intent"] in {"discuss", "clarify"}:
                continue

            feedback = turn["revision_request"].strip()
            if turn["adaptation"] is not None:
                feedback += (
                    "\nRuntime creative adaptation brief:\n"
                    + json.dumps(turn["adaptation"], ensure_ascii=False, indent=2)
                )
            changes_made += 1
            active_request += (
                f"\nParent's exact feedback: {reply}"
                f"\nContextual revision instruction: {feedback}"
            )
            print(
                "I’m revising it now, then I’ll give it another bedtime check…",
                flush=True,
            )
            revised = revise_story(
                active_request,
                current_story,
                classification,
                changes=[],
                user_feedback=feedback,
            )
            current_story = improve_until_acceptable(
                user_id,
                story_id,
                active_request,
                revised,
                classification,
                recent_stories,
            )
            print("\nHere’s the updated story:\n")
            print(current_story)

        print("\nAll set. I’ve saved this story and its revision history for next time.")
    except Exception as error:
        print(f"I’m sorry—I couldn’t finish the story this time. ({error})")


if __name__ == "__main__":
    main()
