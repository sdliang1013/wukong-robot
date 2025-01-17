# coding=utf-8

# Copyright (c) 2018 Baidu, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys

import solr_tools

if sys.argv[1] == "add_engine":
    solr_tools.add_engine(
        sys.argv[2],
        sys.argv[3],
        sys.argv[4],
        shard=1,
        replica=1,
        maxshardpernode=5,
        conf="myconf",
    )
elif sys.argv[1] == "delete_engine":
    solr_tools.delete_engine(sys.argv[2], sys.argv[3], sys.argv[4])
elif sys.argv[1] == "upload_doc":
    solr_tools.upload_documents(
        sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], num_thread=1
    )
elif sys.argv[1] == "clear_doc":
    solr_tools.clear_documents(sys.argv[2], sys.argv[3], sys.argv[4])
