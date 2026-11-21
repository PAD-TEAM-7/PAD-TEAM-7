def verify(demo):
    world = demo.request(f"/api/v1/worlds/{demo.lobby}")
    exam = demo.request(f"/api/v1/exams/{demo.values['examId']}")
    courses = demo.request(f"/api/v1/players/{demo.player}/courses")
    assert world["lobby_id"] == demo.lobby and world["rooms"] and world["home_room_id"]
    assert exam["exam_id"] == demo.values["examId"] and len(exam["questions"]) == 10
    assert courses["items"]
    print("PASS Exam/World: generated world, ten-question exam, and persisted event-driven enrolment.")
