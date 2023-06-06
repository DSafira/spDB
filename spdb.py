import pickle
import faiss
from faiss.contrib.exhaustive_search import knn
import numpy as np

import utils
import lmdb_utils
import input_validation
import train


class spDB:
    def __init__(self, name: str, save_path: str = None, vector_dimension: int = None, max_memory_usage: int = 4 * 1024 * 1024 * 1024):
        self.name = name
        self.save_path = save_path
        self.faiss_index = None
        self.vector_dimension = vector_dimension
        self.max_id = -1
        self.max_memory_usage = max_memory_usage

        lmdb_utils.create_lmdb(save_path, name)

    def save(self):
        # save faiss index and delete (so it doesn't get pickled)
        tmp = self.faiss_index
        if self.faiss_index is not None:
            faiss.write_index(self.faiss_index,
                              f'{self.save_path}{self.name}.index')
            self.faiss_index = None

        # save object to pickle file
        pickle.dump(self, open(f'{self.save_path}{self.name}.pickle', 'wb'))

        self.faiss_index = tmp

    def train(self, use_two_level_clustering: bool = None, pca_dimension: int = 256, opq_dimension: int = 128, compressed_vector_bytes: int = 32):

        # Validate the inputs
        is_valid, reason = input_validation.validate_train(
            self.vector_dimension, pca_dimension, compressed_vector_bytes, opq_dimension)
        if not is_valid:
            raise ValueError(reason)

        # Load the vectors from the LMDB
        vector_ids = lmdb_utils.get_lmdb_index_ids(self.save_path, self.name)
        num_vectors = len(vector_ids)

        if use_two_level_clustering is None:
            # Figure out which training method is optimal based off the max memory usage and number of vectors
            training_method = utils.determine_optimal_training_method(
                self.max_memory_usage, self.vector_dimension, num_vectors)

        if use_two_level_clustering or training_method == 'clustering':
            print('Training with clustering')
            self.faiss_index = train.train_with_two_level_clustering(
                self.save_path, self.name, self.vector_dimension, pca_dimension, opq_dimension, compressed_vector_bytes, self.max_memory_usage)
        else:
            print('Training with subsampling')
            self.faiss_index = train.train_with_subsampling(
                self.save_path, self.name, self.vector_dimension, pca_dimension, opq_dimension, compressed_vector_bytes, self.max_memory_usage)

        self.save()

    def add(self, vectors: np.ndarray, text: list):
        # add vector to faiss index

        # Validate the inputs
        is_valid, reason = input_validation.validate_add(
            vectors, text, self.vector_dimension)
        if not is_valid:
            raise ValueError(reason)

        ids = utils.create_faiss_index_ids(self.max_id, vectors.shape[0])
        self.max_id = ids[-1]

        lmdb_utils.add_vectors_to_lmdb(self.save_path, self.name, vectors, ids)
        lmdb_utils.add_text_to_lmdb(self.save_path, self.name, text, ids)

        # If the index is not trained, don't add the vectors to the index
        if self.faiss_index is not None:
            # TODO: transform vectors if necessary
            self.faiss_index.add_with_ids(vectors, ids)

        self.vector_dimension = vectors.shape[1]
        self.save()

    def remove(self, vector_ids):
        # remove vector from faiss index
        # remove text from LMDB

        pass

    def query(self, query_vector: np.ndarray, top_k: int = 100):

        # query_vector needs to be a 1D array
        is_valid, reason = input_validation.validate_query(query_vector)
        if not is_valid:
            raise ValueError(reason)

        # Check if we need to reshape the query vector
        if len(query_vector.shape) == 1:
            query_vector = query_vector.reshape((-1, self.vector_dimension))

        # query faiss index
        _, I = self.faiss_index.search(query_vector, 500)

        corpus_vectors, position_to_id_map = lmdb_utils.get_ranked_vectors(
            self.save_path, self.name, I)

        # brute force search full vectors to find true top_k
        _, reranked_I = knn(query_vector, corpus_vectors, top_k)

        reranked_text = lmdb_utils.get_reranked_text(
            self.save_path, self.name, reranked_I, position_to_id_map)

        return reranked_text


def load_knowledge_base(name, save_path: str):
    # load KnowledgeBase object from pickle file
    kb = pickle.load(open(f'{save_path}{name}.pickle', 'rb'))

    # load faiss index from save path
    try:
        index = faiss.read_index(f'{kb.save_path}{name}.index')
    except:
        index = None
    kb.faiss_index = index

    return kb