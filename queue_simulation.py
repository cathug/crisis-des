'''
    This program simulates the OpenUp Counselling Service platform and
    helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue
'''

import simpy, random
# from scipy.stats import poisson




# Globals
NUM_HELPSEEKERS_IN_SYSTEM = 0
NUM_HELPSEEKERS_IN_QUEUE = 0
NUM_HELPSEEKERS_IN_PRIORITY_QUEUE = [0, 0, 0]
MEAN_QUEUE_TIME_BEFORE_RENEGING = 7.0 # specify this as a float
LAMBDA_RENEGE = 1.0/MEAN_QUEUE_TIME_BEFORE_RENEGING # mean reneging counts
LUNCH_BREAK = 60 # 60 minute lunch break
MEAN_CHAT_DURATION = 60 # average chat no longer than 60 minutes
SIMULATION_DURATION = 180 #1440
MEAN_INTERARRIVAL_TIME = 7.0 # mean time between helpseekers arriving
NUM_COUNSELLORS = 16
SEED = 728


################################################################################
# drawing arrival times from distribitions
################################################################################

def get_interarrival_time(mean_interarrival_time):
    '''
        Interarrival time getter

        param:
            mean_interarrival_time - mean interarrival time between helpseekers
    '''
    lambda_interarrival_time = 1.0/mean_interarrival_time
    return random.expovariate(lambda_interarrival_time)

#-------------------------------------------------------------------------------

def get_chat_duration(mean_chat_duration):
    '''
        Chat duration getter

        param:
            mean_chat_duration - mean chat duration of a conversation
    '''
    chat_duration_lambda = 1.0 / mean_chat_duration
    return random.expovariate(chat_duration_lambda)

#-------------------------------------------------------------------------------

def get_priority():
    '''
        Helpseeker priority getter

        Distribution of 

    '''

# class Counsellor:
#     '''
#         Counsellor Class with a limited number of counsellors to serve helpseekers
#     '''

#     def __init__(self, 
#                  env,
#                  counsellor_id,
#                  service_duration,
#                  lunch_duration=LUNCH_BREAK):
#         self.env = env
#         self.process = env.process(self.shift() )
#         self.counsellor = f'Counsellor {counsellor_id}'
#         self.service_duration = service_duration
#         self.lunch_duration = lunch_duration

#     #---------------------------------------------------------------------------
    
#     def shift(self):
#         while True:
            
#             # start counselling for the first 6 hours
#             print(f'{self.counsellor} starts shift at {self.env.now}')
#             yield self.env.timeout(self.service_duration)


#             # get lunch break
#             yield self.env.process(self.lunch_break() )

#             print(f'Counsellor returns from lunch at {self.env.now}')
#             yield self.env.timeout(self.service_duration)
            
#             print(f'{self.counsellor} shift ends at {self.env.now}')

    
#     #---------------------------------------------------------------------------

#     def lunch_break(self):
#         '''
#             Give counsellors a lunch break
#             but interrupt when there is an urgent case
#         '''

#         print(f'{self.counsellor} takes a lunch break at {self.env.now}')
        
#         yield self.env.timeout(self.lunch_duration)
#         print(f'{self.counsellor} lunch break ends at {self.env.now}')
        
#         # try:
#         #     yield self.env.timeout(self.lunch_duration)
#         #     print(f'{self.counsellor} lunch break ends at {self.env.now}')
#         # except simpy.Interrupt as i:
#         #     print(f'{self.counsellor} lunch break interrupted at '
#         #         f'{self.env.now} due to {i.cause}')


# #-------------------------------------------------------end of Counsellors class

class Helpseeker:
    '''
        Helpseeker Class to create a helpseeker
    '''

    def __init__(self,
                 env,
                 helpseeker_id,
                 openup_service,
                 mean_chat_duration=MEAN_CHAT_DURATION,
                 mean_interarrival_time=MEAN_INTERARRIVAL_TIME):

        '''
            param:
                env - simpy environment instance
                helpseeker_id - an assigned helpseeker id
                openup_service - Openup counselling service resource instance
                mean_chat_duration - mean chat duration
                mean_interarrival_time - mean interarrival time
        '''

        self.env = env
        self.arrival_time = self.env.now
        self.openup_service = openup_service
        self.user = f'Helpseeker {helpseeker_id}'
        self.mean_chat_duration = mean_chat_duration
        self.mean_interarrival_time = mean_interarrival_time

        # start creating a chatroom session
        self.process = env.process(self.session() )

    #---------------------------------------------------------------------------

    def session(self):
        '''
            Function to create a chatroom session
        '''

        # simulate the interarrivals between users
        interarrival_time = get_interarrival_time(self.mean_interarrival_time)
        yield self.env.timeout(interarrival_time)


        print(f'\n{self.user} has accepted TOS.  '
            f'Chat session created at t = {self.env.now}.')

        with self.openup_service.request() as req:
            # helpseeker patience follows an exponential distribution
            # wait for counsellor or renege
            patience = random.expovariate(LAMBDA_RENEGE)
            results = yield req | self.env.timeout(patience)

            queue_time = self.env.now - self.arrival_time
            
            if req in results:
                # counsellor now serving helpseeker
                print(f'\n{self.user} now being served.')
                chat_duration = get_chat_duration(self.mean_chat_duration)
                yield self.env.timeout(chat_duration)
                print(f'\n{self.user} chat session terminated successfully at t ='
                    f' {self.env.now}.')

            else:
                # helpseeker reneged
                print(f'\n{self.user} reneged after '
                    f'spending t = {self.env.now} in the queue.')

    #---------------------------------------------------------------------------

def main():
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Initializing OpenUp Queue Simulation')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n')
    random.seed(SEED) # comment out line if not reproducing results

    # create environment
    env = simpy.Environment()
    # counsellor = Counsellor(env, 1, 8, 1)
    
    counsellor = simpy.Resource(env, capacity=NUM_COUNSELLORS)

    helpseekers = []
    start_time = env.now
    duration = 0

    for i in range(1, 10):
        helpseekers.append(Helpseeker(env, i, counsellor) )

    print(f'Total number of helpseekers created {len(helpseekers)}\n\n')

    env.run(until=SIMULATION_DURATION) # daily simulation at the queue

if __name__ == '__main__':
    main()






# class QueueSimulationModel:
#     '''
#         The Queue Simulation Model
#     '''

#     def __init(self):
#         self.env = sp.Environment() # the execution environment
