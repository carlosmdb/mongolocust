from locust import task, between

from mongo_user import MongoUser
from settings import COLLECTION_NAME

import pymongo
import random
import os

# docs to insert per batch insert
DOCS_PER_BATCH = int(os.environ.get('DOCS_PER_BATCH')) if os.environ.get('MONGO_URI') else 100

# number of cache entries for queries
NAMES_TO_CACHE = 1000

INSERT_WEIGHT = int(os.environ.get('INSERT_WEIGHT')) if os.environ.get('INSERT_WEIGHT') else 3
FIND_WEIGHT = int(os.environ.get('FIND_WEIGHT')) if os.environ.get('FIND_WEIGHT') else 1
BULK_WEIGHT = int(os.environ.get('BULK_WEIGHT')) if os.environ.get('BULK_WEIGHT') else 1
AGG_WEIGHT = int(os.environ.get('AGG_WEIGHT')) if os.environ.get('AGG_WEIGHT') else 1

class MongoSampleUser(MongoUser):
    """
    Generic sample mongodb workload generator
    """
    # no delays between operations
    wait_time = between(0.0, 0.0)

    def __init__(self, environment):
        super().__init__(environment)
        self.name_cache = []

    def generate_new_document(self):
        """
        Generate a new sample document
        """
        document = {
            'first_name': self.faker.first_name(),
            'last_name': self.faker.last_name(),
            'address': self.faker.street_address(),
            'city': self.faker.city(),
            'total_assets': self.faker.pydecimal(min_value=100, max_value=1000, right_digits=2)
        }
        return document

    def run_aggregation_pipeline(self):
        """
        Run an aggregation pipeline on a secondary node
        """
        # count number of inhabitants per city
        group_by = {
            '$group': {
                '_id': '$city',
                'total_inhabitants': {'$sum': 1}
            }
        }

        # rename the _id to city
        set_columns = {'$set': {'city': '$_id'}}
        unset_columns = {'$unset': ['_id']}

        # sort by the number of inhabitants desc
        order_by = {'$sort': {'total_inhabitants': pymongo.DESCENDING}}

        pipeline = [group_by, set_columns, unset_columns, order_by]

        # make sure we fetch everything by explicitly casting to list
        # use self.collection instead of self.collection_secondary to run the pipeline on the primary
        return list(self.collection_secondary.aggregate(pipeline))

    def on_start(self):
        """
        Executed every time a new test is started - place init code here
        """
        # prepare the collection
        index1 = pymongo.IndexModel([('first_name', pymongo.ASCENDING), ("last_name", pymongo.DESCENDING)],
                                    name="idx_first_last")
        self.collection, self.collection_secondary = self.ensure_collection(COLLECTION_NAME, [index1])
        self.name_cache = []

    def insert_single_document(self):
        document = self.generate_new_document()

        # cache the first_name, last_name tuple for queries
        cached_names = (document['first_name'], document['last_name'])
        if len(self.name_cache) < NAMES_TO_CACHE:
            self.name_cache.append(cached_names)
        else:
            if random.randint(0, 9) == 0:
                self.name_cache[random.randint(0, len(self.name_cache) - 1)] = cached_names

        self.collection.insert_one(document)

    def find_document(self):
        # at least one insert needs to happen
        if not self.name_cache:
            return

        # find a random document using an index
        cached_names = random.choice(self.name_cache)
        self.collection.find_one({'first_name': cached_names[0], 'last_name': cached_names[1]})

    @task(weight=FIND_WEIGHT)
    def do_find_document(self):
        self._process('find-document', self.find_document)

    @task(weight=INSERT_WEIGHT)
    def do_insert_document(self):
        self._process('insert-document', self.insert_single_document)

    @task(weight=BULK_WEIGHT)
    def do_insert_document_bulk(self):
        self._process('insert-document-bulk', lambda: self.collection.insert_many(
            [self.generate_new_document() for _ in
             range(DOCS_PER_BATCH)], ordered=False), DOCS_PER_BATCH)

    @task(weight=AGG_WEIGHT)
    def do_run_aggregation_pipeline(self):
        self._process('run-aggregation-pipeline', self.run_aggregation_pipeline)
