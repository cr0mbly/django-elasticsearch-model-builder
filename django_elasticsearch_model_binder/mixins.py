from datetime import datetime

from django.db.models.manager import Manager
from django.db import models
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from .exceptions import (
    NominatedFieldDoesNotExistForESIndexingException,
    UnableToBulkIndexModelsToElasticSearch,
    UnableToCastESNominatedFieldToStringException,
    UnableToDeleteModelFromElasticSearch,
    UnableToSaveModelToElasticSearch,
)

class ESModelBinderMixin(models.Model):
    """
    Mixin that binds a models nominated field to an Elasticsearch index.
    Nominated fields will maintain persistency with the models existence
    and configuration within the database.
    """

    index_name = None
    es_cached_fields = []

    class Meta:
        abstract = True

    @classmethod
    def convert_to_indexable_format(cls, value):
        """
        Helper method to cast an incoming value into a format that
        is indexable within ElasticSearch. extend with your own super
        implentation if there are custom types you'd like handled differently.
        """
        if isinstance(value, models.Model):
            return value.pk
        elif isinstance(value, datetime):
            return value.strftime('%Y-%M-%d %H:%M:%S')
        else:
            # Catch all try to cast value to string raising
            # an exception explicitly if that fails.
            try:
                return str(value)
            except Exception:
                raise UnableToCastESNominatedFieldToStringException()

    @classmethod
    def get_index_name(cls):
        """
        Retrieve the model defined index name from self.index_name defaulting
        to generated name based on app module directory and model name.
        """
        if cls.index_name:
            return index_name
        else:
            return '-'.join(
                cls.__module__.lower().split('.')
                + [cls.__class__.__name__.lower()]
            )

    def save(self, *args, **kwargs):
        """
        Override model save to index those fields nominated by es_cached_fields
        storring them in elasticsearch.
        """
        super().save(*args, **kwargs)

        document = {}
        for field in self.es_cached_fields:
            if not hasattr(self, field):
                raise NominatedFieldDoesNotExistForESIndexingException()

            document[field] = self.convert_to_indexable_format(
                getattr(self, field)
            )

        try:
            self.get_es_client().index(
                index=self.get_index_name(),
                doc_type=self.__class__.__name__,
                id=self.pk,
                body=document
            )
        except Exception:
            raise UnableToSaveModelToElasticSearch()

    def delete(self, *args, **kwargs):
        """
        Same as save but in reverse, remove the model instances cached
        fields in Elasticsearch.
        """
        try:
            self.get_es_client().delete(
                index=self.get_index_name(), id=self.pk,
            )
        except Exception:
            # Catch failure and reraise with specific exception.
            raise UnableToDeleteModelFromElasticSearch()

        super().save(*args, **kwargs)

    @classmethod
    def get_es_client(cls):
        """
        Return the elasticsearch client instance, allows implementer to extend
        mixin here replacing this implementation with one more suited to
        their use case.
        """
        if not hasattr(settings, 'DJANGO_ES_MODEL_CONFIG'):
            raise ImproperlyConfigured(
                'DJANGO_ES_MODEL_CONFIG must be defined in app settings'
            )

        return Elasticsearch(**settings.DJANGO_ES_MODEL_CONFIG)


class ESQuerySetMixin:
    """
    Mixin for providing Elasticsearch bulk indexing
    implementation to querysets.
    """

    def reindex_into_es(self):
        """
        Bulk reindex all nominated fields into elasticsearch
        """
        queryset_values = self.values(
            *list(set(['pk', *self.model.es_cached_fields]))
        )

        documents = []
        for model_values in queryset_values:
            doc = {'_id': model_values['pk']}
            if 'pk' not in self.model.es_cached_fields:
                doc['_source'] = model_values
            documents.append(doc)

        try:
            bulk(
                self.model.get_es_client(), documents,
                index=self.model.get_index_name(),
                doc_type=self.model.__name__
            )
        except Exception:
            raise UnableToBulkIndexModelsToElasticSearch()
