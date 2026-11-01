from ai_support import is_question_message


def test_detects_questions_in_supported_languages():
    assert is_question_message("How do I join the server?")
    assert is_question_message("Wie kann ich dem Server beitreten?")
    assert is_question_message("¿Dónde está el Discord?")
    assert is_question_message("Pourquoi le bot ne répond pas ?")


def test_ignores_commands_and_regular_statements():
    assert not is_question_message("!help")
    assert not is_question_message(".ticket")
    assert not is_question_message("Good morning everyone")


def test_attachment_questions_are_detected():
    assert is_question_message("Can someone check this", has_attachments=True)
    assert is_question_message("", has_attachments=True)
