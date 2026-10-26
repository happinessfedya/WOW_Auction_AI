import unittest
from unittest.mock import patch

from wow_auction.lua_data import LuaDataError, ParseLimits, parse_saved_variables


class LuaDataTests(unittest.TestCase):
    def assert_error(self, data, code=None, limits=None):
        with self.assertRaises(LuaDataError) as raised:
            parse_saved_variables(data, limits)
        if code:
            self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_nested_literals_nil_false_and_zero(self):
        parsed = parse_saved_variables(b'DB = { ["cash"] = 0, flag = false, absent = nil, {"a", nil, "b"}, [10] = true }; Empty = nil')
        self.assertEqual(parsed, {"DB": {"cash": 0, "flag": False, 1: {1: "a", 3: "b"}, 10: True}})

    def test_decimal_bytes_decode_as_one_utf8_string(self):
        self.assertEqual(parse_saved_variables(br'DB = { ["\208\144"] = "\208\145\0009" }'), {"DB": {"А": "Б\x009"}})

    def test_all_lua51_named_escapes(self):
        self.assertEqual(parse_saved_variables(br'''DB = "\a\b\f\n\r\t\v\\\"\'"''')["DB"], '\a\b\f\n\r\t\v\\"\'')

    def test_physical_escaped_line_endings(self):
        self.assertEqual(parse_saved_variables(b'DB="a\\\r\nb\\\nc"')["DB"], "a\nb\nc")

    def test_comments_long_brackets_bom_and_newline_normalization(self):
        data = b'\xef\xbb\xbf-- comment\r\n--[=[ hidden end ]=]\nDB = [==[\r\nline\rnext\n\rlast\\n]==]'
        self.assertEqual(parse_saved_variables(data), {"DB": "line\nnext\nlast\\n"})

    def test_long_string_array_field_and_quoted_bracket_key(self):
        self.assertEqual(parse_saved_variables(b'DB={[[value]], [ [=[key]=] ]=3,}'), {"DB": {1: "value", "key": 3}})

    def test_consecutive_mixed_newlines_are_not_collapsed(self):
        self.assertEqual(parse_saved_variables(b'DB=[[x\r\n\ry\n\r\nz]]'), {"DB": "x\n\ny\n\nz"})

    def test_numeric_forms_and_integer_key_normalization(self):
        # Explicit index 3 duplicates the third implicit field: reject ambiguity.
        self.assert_error(b'DB={0x10, -0Xf, -12, .5, 1.5e+2, [3.0]=8}', 'duplicate_table_key')

    def test_numeric_forms(self):
        self.assertEqual(parse_saved_variables(b'DB={0x10,-0Xf,-12,.5,1.5e+2,[9.0]=8}'),
                         {"DB": {1:16, 2:-15, 3:-12, 4:0.5, 5:150.0, 9:8}})

    def test_duplicate_field_global_and_numeric_alias_rejected(self):
        for data in (b'DB={a=1,a=2}', b'DB={a=nil,a=2}', b'DB=1 DB=2',
                     b'DB={[1]=2,[1.0]=3}', b'DB={[1]=nil,3}'):
            with self.subTest(data=data):
                self.assert_error(data)

    def test_code_references_and_expressions_rejected(self):
        for data in (b'DB=os.execute("private payload")', b'DB=setmetatable({}, {})',
                     b'DB={1+1}', b'DB=(1)', b'local DB={}', b'DB=other',
                     b'DB={}; DB[1]=2', b'DB=function()end', b'while true do end',
                     b'DB={}; return DB', b'DB="a".."b"'):
            with self.subTest(data=data):
                self.assert_error(data)

    def test_all_truncations_of_structure_rejected(self):
        data = b'DB={nested={key="value", list={1,2,3}}}'
        for length in range(1, len(data)):
            with self.subTest(length=length):
                self.assert_error(data[:length])
        self.assertIn("DB", parse_saved_variables(data))

    def test_invalid_utf8_raw_and_escaped_is_not_replaced(self):
        for data in (b'DB="\xff"', b'--\xff\nDB={}', br'DB="\255"', br'DB={ ["\208"] = 1 }'):
            with self.subTest(data=data):
                self.assert_error(data)

    def test_invalid_escapes_and_unescaped_newline(self):
        for data in (br'DB="\256"', br'DB="\x41"', b'DB="a\nb"', br'DB="\q"'):
            with self.subTest(data=data):
                self.assert_error(data)

    def test_unsupported_key_types_rejected(self):
        for data in (b'DB={[true]=1}', b'DB={[nil]=1}', b'DB={[1.5]=1}', b'DB={[{}]=1}'):
            with self.subTest(data=data):
                self.assert_error(data, "unsupported_table_key")

    def test_unterminated_long_comment_rejected(self):
        self.assert_error(b'--[==[ unfinished', 'unterminated_long_string')

    def test_error_never_includes_private_source(self):
        error = self.assert_error(b'DB = private_account_name()')
        self.assertNotIn("private_account_name", str(error))
        self.assertIsInstance(error.offset, int)

    def test_size_depth_node_string_and_number_limits(self):
        self.assert_error(b'DB="12345"', "size_limit", ParseLimits(max_bytes=4))
        self.assert_error(b'DB={{{}}}', "depth_limit", ParseLimits(max_depth=2))
        self.assert_error(b'DB={1,2}', "node_limit", ParseLimits(max_nodes=2))
        self.assert_error(b'DB="12345"', "string_limit", ParseLimits(max_string_bytes=4))
        self.assert_error(b'DB=[[12345]]', "string_limit", ParseLimits(max_string_bytes=4))
        self.assert_error(b'DB=12345', "number_limit", ParseLimits(max_number_chars=4))

    def test_extreme_depth_does_not_raise_recursion_error(self):
        self.assert_error(b'DB=' + b'{' * 5000 + b'}' * 5000, "depth_limit")

    def test_time_budget(self):
        with patch("wow_auction.lua_data.time.monotonic", side_effect=[0, 6]):
            self.assert_error(b'DB={}', "time_limit")

    def test_nonfinite_and_malformed_numbers_rejected(self):
        for data in (b'DB=1e999', b'DB=nan', b'DB=0x', b'DB=1e', b'DB=1.2.3', b'DB=1other=2'):
            with self.subTest(data=data):
                self.assert_error(data)

    def test_limit_configuration_rejected(self):
        for kwargs in ({"max_bytes":0}, {"max_depth":101}, {"max_seconds":float("nan")}, {"max_nodes":True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ParseLimits(**kwargs)


if __name__ == "__main__":
    unittest.main()
