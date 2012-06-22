# upconvert.py - A universal hardware design file format converter using
# Format:       upverter.com/resources/open-json-format/
# Development:  github.com/upverter/schematic-file-converter
#
# Copyright 2011 Upverter, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import os, sys

from upconvert.parser.specctra import DsnParser

class DsnParserTests(unittest.TestCase):
    def test_plain_parser(self):
        parser = DsnParser()
        got = parser.parse('''
(pcb test.dsn
  (parser
    (host_cad "Kicad's PCBNEW")
    (host_version "(2011-06-28)")
  )
)''')
        correct = ['pcb', 'test.dsn',
                    ['parser',
                        ['host_cad', "\"Kicad's", "PCBNEW\""],
                        ['host_version', '"', ['2011-06-28'], '"']
                    ]
                ]
 
        self.assertEqual(correct, got)

    def test_quote_parser(self):
        parser = DsnParser()
        got = parser.parse('''
(pcb test.dsn
  (parser
    (string_quote ")
    (host_cad "Kicad's PCBNEW)
    (host_version "(2011-06-28)")
  )
)''')
        correct = ['pcb', 'test.dsn',
                    ['parser',
                        ['string_quote', '"'],
                        ['host_cad', "Kicad's", "PCBNEW"],
                        ['host_version', '(2011-06-28)']
                    ]
                ]
 
        self.assertEqual(correct, got)

    def test_quote_space_parser(self):
        parser = DsnParser()
        got = parser.parse('''
(pcb test.dsn
  (parser
    (string_quote ")
    (space_in_quoted_tokens on)
    (host_cad "Kicad's PCBNEW")
    (host_version "(2011-06-28)")
  )
)''')
        correct = ['pcb', 'test.dsn',
                    ['parser',
                        ['string_quote', '"'],
                        ['space_in_quoted_tokens', 'on'],
                        ['host_cad', "Kicad's PCBNEW"],
                        ['host_version', '(2011-06-28)']
                    ]
                ]
 
        self.assertEqual(correct, got)

    def test_quoted_string_part(self):
        parser = DsnParser()
        got = parser.parse('''
(pcb test.dsn
  (parser
    (string_quote ")
    (space_in_quoted_tokens on)
    (host_cad "Kicad's PCBNEW")
    (host_version "(2011-06-28)")
  )
  (pins A1-"-")
)''')
        correct = ['pcb', 'test.dsn',
                    ['parser',
                        ['string_quote', '"'],
                        ['space_in_quoted_tokens', 'on'],
                        ['host_cad', "Kicad's PCBNEW"],
                        ['host_version', '(2011-06-28)']
                    ],
                    ['pins', 'A1--'],
                ]
 
        self.assertEqual(correct, got)
