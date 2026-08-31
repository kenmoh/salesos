from app.core.responses import (
    DataMessageResponse,
    DataResponse,
    PaginatedResponse,
    ok,
    paginated,
)


class TestOk:
    def test_data_only(self):
        result = ok({"key": "value"})
        assert result == {"message": "ok", "data": {"key": "value"}}

    def test_with_message(self):
        result = ok(None, message="hello")
        assert result == {"message": "hello", "data": None}

    def test_list_data(self):
        result = ok([1, 2, 3])
        assert result == {"message": "ok", "data": [1, 2, 3]}

    def test_bool_success(self):
        result = ok({"success": True})
        assert result == {"message": "ok", "data": {"success": True}}

    def test_empty_dict(self):
        result = ok({})
        assert result == {"message": "ok", "data": {}}


class TestPaginated:
    def test_basic_pagination(self):
        result = paginated(["a", "b"], total=10, page=1, page_size=2)
        assert result == {"message": "ok", "data": ["a", "b"], "total": 10, "page": 1, "page_size": 2}

    def test_empty_items(self):
        result = paginated([], total=0, page=1, page_size=50)
        assert result == {"message": "ok", "data": [], "total": 0, "page": 1, "page_size": 50}

    def test_with_message(self):
        result = paginated([1], total=1, page=1, page_size=1, message="done")
        assert result == {"message": "done", "data": [1], "total": 1, "page": 1, "page_size": 1}

    def test_items_converted_to_list(self):
        result = paginated((1, 2), total=2, page=1, page_size=2)
        assert result["data"] == [1, 2]


class TestDataResponseModel:
    def test_serialization(self):
        m = DataResponse[dict](data={"x": 1})
        assert m.model_dump() == {"message": "ok", "data": {"x": 1}}

    def test_none_data(self):
        m = DataResponse[type(None)](data=None)
        assert m.model_dump() == {"message": "ok", "data": None}


class TestDataMessageResponseModel:
    def test_serialization(self):
        m = DataMessageResponse[dict](data={"x": 1}, message="ok")
        assert m.model_dump() == {"message": "ok", "data": {"x": 1}}

    def test_none_data(self):
        m = DataMessageResponse[type(None)](data=None, message="msg")
        assert m.model_dump() == {"message": "msg", "data": None}


class TestPaginatedResponseModel:
    def test_serialization(self):
        m = PaginatedResponse[dict](data=[{"a": 1}], total=1, page=1, page_size=10)
        assert m.model_dump() == {
            "message": "ok",
            "data": [{"a": 1}],
            "total": 1,
            "page": 1,
            "page_size": 10,
        }

    def test_with_message(self):
        m = PaginatedResponse[dict](data=[], total=0, page=1, page_size=10, message="no results")
        assert m.model_dump()["message"] == "no results"
