import os
import unittest
from unittest import mock

from finance_tracker import feishu_client


class FeishuClientNetworkTest(unittest.TestCase):
    def test_feishu_domains_are_added_to_existing_no_proxy(self):
        with mock.patch.dict(
            os.environ,
            {"NO_PROXY": "localhost,127.0.0.1"},
            clear=False,
        ):
            value = feishu_client.configure_feishu_network()
            entries = value.split(",")
            self.assertIn("localhost", entries)
            self.assertIn("127.0.0.1", entries)
            self.assertIn("open.feishu.cn", entries)
            self.assertIn(".feishu.cn", entries)
            self.assertEqual(os.environ["NO_PROXY"], value)
            self.assertEqual(os.environ["no_proxy"], value)

    def test_repeated_configuration_does_not_duplicate_hosts(self):
        with mock.patch.dict(os.environ, {"NO_PROXY": ""}, clear=False):
            first = feishu_client.configure_feishu_network()
            second = feishu_client.configure_feishu_network()
            self.assertEqual(first, second)
            self.assertEqual(
                second.split(",").count("open.feishu.cn"),
                1,
            )


if __name__ == "__main__":
    unittest.main()
