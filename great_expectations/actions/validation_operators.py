import logging
logger = logging.getLogger(__name__)

import importlib
import pandas as pd
import json
from six import string_types

import great_expectations as ge
from ..util import (
    get_class_from_module_name_and_class_name,
)
from great_expectations.data_context.util import (
    instantiate_class_from_config,
)
from ..data_context.types import (
    DataAssetIdentifier,
    ValidationResultIdentifier,
    ExpectationSuiteIdentifier,
)
from great_expectations.data_asset import (
    DataAsset,
)

# NOTE: Abe 2019/08/24 : This is first implementation of all these classes. Consider them UNSTABLE for now. 

class ValidationOperator(object):
    """Zoned for expansion"""
    pass

# TODO : Tidy up code smells here...

class ActionAwareValidationOperator(ValidationOperator):

    def validate_and_take_action(self, batch, expectation_purpose="default"):
        raise NotImplementedError

class DefaultActionAwareValidationOperator(ActionAwareValidationOperator):
    pass

class DataContextAwareValidationOperator(ActionAwareValidationOperator):
    def __init__(self,
        data_context,
        action_list,
        expectation_suite_name_prefix="",
    ):
        self.data_context = data_context

        self.action_list = action_list
        self.actions = {}
        for action_config in action_list:
            assert isinstance(action_config, dict)
            if not set(action_config.keys()) == set(["name", "result_key", "action"]):
                raise KeyError('Action config keys must be ("name", "result_key", "action"). Instead got {}'.format(action_config.keys()))

            new_action = instantiate_class_from_config(
                config=action_config["action"],
                runtime_config={
                    "data_context": self.data_context,
                },
                config_defaults={
                    "module_name": "great_expectations.actions"
                }
            )
            self.actions[action_config["name"]] = new_action
        
        self.expectation_suite_name_prefix = expectation_suite_name_prefix


    # NOTE : Abe 2019/09/12 : It isn't clear to me that this method should live in ValidationOperators.
    # Even though it's invoked from there, it feels more like a DataContext concern.
    # FIXME : This method isn't fully implemented or tested yet.
    def _get_or_convert_to_batch_from_identifiers(self,
        data_asset=None, # A data asset that COULD be a batch, OR a generic data asset
        data_asset_id_string=None, # If data_asset isn't a batch, then this
        data_asset_identifier=None, # ... or this is required
        run_identifier=None,
    ):
        if not data_asset is None:
            # Get a valid data_asset_identifier or raise an error
            # if isinstance(data_asset, Batch):
            if hasattr(data_asset, "data_asset_name") and isinstance(data_asset.data_asset_name, DataAssetIdentifier):
                data_asset_identifier = data_asset.data_asset_identifier

            else:
                if data_asset_identifier is not None:
                    assert isinstance(data_asset_identifier, DataAssetIdentifier)

                elif data_asset_id_string is not None:
                    data_asset_identifier = self._normalize_data_asset_name(data_asset_id_string)

                else:
                    raise ValueError("convert_to_batch couldn't identify a data_asset_name from arguments {}".format({
                        "type(data_asset)" : type(data_asset),
                        "data_asset_id_string" : data_asset_id_string,
                        "data_asset_identifier" : data_asset_identifier,
                        "run_identifier" : run_identifier,
                    }))

            if hasattr(data_asset, "run_id") and isinstance(data_asset.run_id, string_types):
                run_id = data_asset.run_id

            # We already have a data_asset identifier, so no further action needed.
            batch = data_asset

        else:
            if data_asset_identifier is not None:
                assert isinstance(data_asset_identifier, DataAssetIdentifier)

            elif data_asset_id_string is not None:
                data_asset_identifier = self._normalize_data_asset_name(data_asset_id_string)

            else:
                raise ValueError("convert_to_batch couldn't identify a data_asset_name from arguments {}".format({
                    "type(data_asset)" : type(data_asset),
                    "data_asset_id_string" : data_asset_id_string,
                    "data_asset_identifier" : data_asset_identifier,
                    "run_identifier" : run_identifier,
                }))

            # FIXME: Need to look up the API for this.
            batch = self.get_batch(data_asset_identifier, run_identifier)

        # At this point, we should have a properly typed and instantiated data_asset_identifier and run_identifier
        assert isinstance(data_asset_identifier, DataAssetIdentifier)
        assert isinstance(run_identifier, string_types)

        assert isinstance(batch, DataAsset)

        # NOTE: We don't have a true concept of a Batch aka DataContextAwareDataAsset yet.
        # So attaching a data_asset_id and run_id to a generic DataAsset is the best we can do.
        # This should eventually be switched to a properly typed class.
        batch.data_asset_identifier = data_asset_identifier
        batch.run_id = run_identifier

        return batch

    def run(self,
        data_asset=None, # A data asset that COULD be a batch, OR a generic data asset
        data_asset_id_string=None, # If data_asset isn't a batch, then this
        data_asset_identifier=None, # ... or this is required
        run_identifier=None,
    ):
        batch = self._get_or_convert_to_batch_from_identifiers(
            data_asset=data_asset,
            data_asset_id_string=data_asset_id_string,
            data_asset_identifier=data_asset_identifier,
            run_identifier=run_identifier,
        )
        return self._run(batch)

    def _run(self, batch):
        raise NotImplementedError


#class DefaultDataContextAwareValidationOperator(DataContextAwareValidationOperator, DefaultActionAwareValidationOperator):
class DefaultDataContextAwareValidationOperator(DataContextAwareValidationOperator):

    def __init__(self,
        data_context,
        action_list,
        expectation_suite_name_prefix="",
        expectation_suite_name_suffixes=["failure", "warning", "quarantine"],
        process_warnings_and_quarantine_rows_on_error=False,
    ):
        super(DefaultDataContextAwareValidationOperator, self).__init__(
            data_context,
            action_list,
        )
        
        self.process_warnings_and_quarantine_rows_on_error = process_warnings_and_quarantine_rows_on_error
        self.expectation_suite_name_prefix = expectation_suite_name_prefix

        assert len(expectation_suite_name_suffixes) == 3
        for suffix in expectation_suite_name_suffixes:
            assert isinstance(suffix, string_types)
        self.expectation_suite_name_suffixes = expectation_suite_name_suffixes


    def _run(self, batch):
        # Get batch_identifier.
        # TODO : We should be using typed batch
        # if data_asset_identifier is None:
        data_asset_identifier = batch.data_asset_identifier
        run_id = batch.run_id

        assert not data_asset_identifier is None
        assert not run_id is None

        # NOTE : Abe 2019/09/12 : Perhaps this could be generalized to a loop.
        # I'm NOT doing that, because lots of user research suggests that these 3 specific behaviors
        # (failure, warning, quarantine) will cover most of the common use cases for
        # post-validation dat treatment.
        # TODO: Make it possible to override "failure", "warning", "quarantine" as default strings
        failure_validation_result_id = ValidationResultIdentifier(
            expectation_suite_identifier=ExpectationSuiteIdentifier(
                data_asset_name=data_asset_identifier,
                expectation_suite_name=self.expectation_suite_name_prefix+"failure"
            ),
            run_id=run_id,
        )
        warning_validation_result_id = ValidationResultIdentifier(
            expectation_suite_identifier=ExpectationSuiteIdentifier(
                data_asset_name=data_asset_identifier,
                expectation_suite_name=self.expectation_suite_name_prefix+"warning"
            ),
            run_id=run_id,
        )
        quarantine_validation_result_id = ValidationResultIdentifier(
            expectation_suite_identifier=ExpectationSuiteIdentifier(
                data_asset_name=data_asset_identifier,
                expectation_suite_name=self.expectation_suite_name_prefix+"quarantine"
            ),
            run_id=run_id,
        )

        failure_expectations = self._get_expectation_suite_by_validation_result_id(failure_validation_result_id)
        warning_expectations = self._get_expectation_suite_by_validation_result_id(warning_validation_result_id)
        quarantine_expectations = self._get_expectation_suite_by_validation_result_id(quarantine_validation_result_id)

        # NOTE : Abe 2019/09/12: We should consider typing this object, since it's passed between classes.
        # Maybe use a Store, since it's a key-value thing...?
        # For now, I'm NOT typing it until we gain more practical experience with operators and actions.
        return_obj = {
            "success" : None,
            "validation_results" : {
                # NOTE : storing validation_results as (id, value) tuples is a temporary fix,
                # until we implement typed ValidationResultSuite objects.
                "failure" : (failure_validation_result_id, None),
                "warning" : (warning_validation_result_id, None),
                "quarantine" : (quarantine_validation_result_id, None),
            },
            "data_assets" : {
                "original_batch" : batch,
                "nonquarantined_batch" : None,
                "quarantined_batch" : None,
            }
        }

        return_obj["validation_results"]["failure"] = (failure_validation_result_id, batch.validate(failure_expectations))
        return_obj["success"] = return_obj["validation_results"]["failure"][1]["success"]

        #TODO: Add checking for exceptions in Expectations
        if return_obj["success"] == False:
            if self.process_warnings_and_quarantine_rows_on_error == False:

                #Process actions here
                self._process_actions(return_obj)
                
                return return_obj

        print(warning_expectations)
        print(batch.validate(warning_expectations))
        return_obj["validation_results"]["warning"] = (warning_validation_result_id, batch.validate(warning_expectations))
        return_obj["validation_results"]["quarantine"] = (quarantine_validation_result_id, batch.validate(quarantine_expectations, result_format="COMPLETE"))
        
        print(return_obj["validation_results"]["quarantine"])
        #TODO: Add checking for exceptions in Expectations

        unexpected_index_set = self._get_unexpected_indexes(*return_obj["validation_results"]["quarantine"])
        # TODO: Generalize this method to work with any data asset, not just pandas.
        return_obj["data_assets"]["quarantined_batch"] = batch.loc[unexpected_index_set]
        return_obj["data_assets"]["nonquarantined_batch"] = batch.loc[set(batch.index) - set(unexpected_index_set)]

        print("Validation successful")
        
        #Process actions here
        # TODO: This should include the whole return object, not just validation_results.
        self._process_actions(return_obj)#, self.config[action_set_name])

        # NOTE : We should consider typing this object, since it's passed between classes.
        return return_obj
        # return {
        #     "validation_results" : validation_result_dict,
        #     # "non_quarantine_dataframe" : 
        #     "quarantine_data_frame": quarantine_df,
        # }
    
    def _get_expectation_suite_by_validation_result_id(
        self,
        validation_result_id
    ):
        # FIXME: This is a dummy method to quickly generate some ExpectationSuites
        df = pd.DataFrame({"x": range(10)})
        ge_df = ge.from_pandas(df)
        ge_df.expect_column_to_exist("x")
        ge_df.expect_column_values_to_be_in_set("x", [1,2,3,4])
        dummy_expectation_suite = ge_df.get_expectation_suite(discard_failed_expectations=False)
        return dummy_expectation_suite

        # TODO: Here's the REAL method.
        # return self.data_context.stores["expectations_suite"].get(
        #     validation_result_id.expectation_suite_identifier
        # )
        # validation_result
        #     ExpectationSuiteIdentifier(**{
        #        data_asset_identifier=data_asset_identifier,
        #        expectation_suite_name=self.expectation_suite_name_prefix+expectation_suite_level,
        #     })

    # TODO: test this method
    # TODO: Generalize this method to work with any data asset, not just pandas.
    def _get_unexpected_indexes(self, validation_result_id, validation_result_suite):
        unexpected_index_set = set()

        for expectation_validation_result in validation_result_suite["results"]:
            # TODO : Add checking for column_map Expectations
            if expectation_validation_result["success"] == False:
                unexpected_index_set = unexpected_index_set.union(expectation_validation_result["result"]["unexpected_index_list"])

        return unexpected_index_set


    # def _process_actions(self, validation_results, action_set_config):
    #     for k,v in action_set_config.items():
    #         print(k,v)
    #         loaded_module = importlib.import_module(v.pop("module_name"))
    #         action_class = getattr(loaded_module, v.pop("class_name"))

    #         action = action_class(
    #             ActionInternalConfig(**v["kwargs"]),
    #             stores = self.context.stores,
    #             services = {},
    #         )
    #         action.take_action(
    #             validation_result_suite=validation_results,
    #             # FIXME : Shouldn't be hardcoded
    #             validation_result_suite_identifier=ValidationResultIdentifier(
    #                 from_string="ValidationResultIdentifier.a.b.c.quarantine.prod-100"
    #             )
    #         )

    # TODO: test this method
    def _process_actions(self, result_obj):
        for action in self.action_list:
            logger.debug("Processing action with name {}".format(action["name"]))
            
            if action["result_key"] != None:
                # TODO : Instead of hardcoding "validation_results", allow result_keys to
                # express nested lookups by using . as a separator:
                # "validation_results.warning"
                validation_result_suite = result_obj["validation_results"][action["result_key"]]

                self.actions[action["name"]].take_action(
                    validation_result_id=validation_result_suite[0],
                    validation_result_suite=validation_result_suite[1],
                )

            else:
                # TODO : Review this convention and see if we like it...
                self.actions[action["name"]].take_action(None, None)

